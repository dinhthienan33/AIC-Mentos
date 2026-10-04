import io
import logging
import os
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Sequence, Tuple

import torch
import torch.nn.functional as F
from PIL import Image

logger = logging.getLogger(__name__)

_PREPROCESS_WORKERS = int(os.getenv("PREPROCESS_WORKERS", "16"))


# ---------------------------------------------------------------------------
# Jina CLIP v2 config
# ---------------------------------------------------------------------------
DEFAULT_JINA_MODEL_ID = "jinaai/jina-clip-v2"
JINA_FULL_DIM = 1024
MATRYOSHKA_DIMS = [32, 64, 128, 256, 512, 768, 1024]

# ---------------------------------------------------------------------------
# OpenCLIP config
# ---------------------------------------------------------------------------
DEFAULT_OPENCLIP_ARCH = "ViT-L-14"
DEFAULT_OPENCLIP_PRETRAINED = "datacomp_xl_s13b_b90k"
OPENCLIP_DIM = 768


def _patch_clip_loss_import() -> None:
    """jina-clip-v2 remote code imports clip_loss, removed in transformers 5.x."""
    import transformers.models.clip.modeling_clip as clip_modeling

    if not hasattr(clip_modeling, "clip_loss") and hasattr(clip_modeling, "image_text_contrastive_loss"):
        clip_modeling.clip_loss = clip_modeling.image_text_contrastive_loss


def get_embedding_dim(default: Optional[int] = None) -> Optional[int]:
    raw = os.getenv("EMBEDDING_DIM", "")
    if raw.strip() == "":
        return default
    dim = int(raw)
    if dim >= JINA_FULL_DIM:
        return None
    return dim


_RESOLVED_DEVICE: Optional[torch.device] = None


def _cuda_usable() -> bool:
    if not torch.cuda.is_available():
        return False
    try:
        probe = torch.tensor([1.0], device="cuda")
        probe.mul_(2)
        torch.cuda.synchronize()
        return True
    except Exception:
        return False


def get_device(device: Optional[torch.device] = None) -> torch.device:
    """Return CUDA when available and usable, otherwise CPU."""
    global _RESOLVED_DEVICE
    if device is not None:
        return device
    if _RESOLVED_DEVICE is not None:
        return _RESOLVED_DEVICE

    force_cpu = os.getenv("FORCE_CPU", "").lower() in ("1", "true", "yes")
    if force_cpu:
        logger.info("FORCE_CPU set; using CPU")
        _RESOLVED_DEVICE = torch.device("cpu")
        return _RESOLVED_DEVICE

    if _cuda_usable():
        name = torch.cuda.get_device_name(0)
        logger.info("Using GPU: %s", name)
        _RESOLVED_DEVICE = torch.device("cuda")
        return _RESOLVED_DEVICE

    if torch.cuda.is_available():
        warnings.warn(
            "CUDA is visible but not usable (missing kernels or driver error); falling back to CPU.",
            stacklevel=2,
        )
    else:
        logger.info("CUDA not available; using CPU")
    _RESOLVED_DEVICE = torch.device("cpu")
    return _RESOLVED_DEVICE


def _setup_cuda_optimizations(device: torch.device) -> None:
    if device.type != "cuda":
        return
    torch.backends.cudnn.benchmark = True
    if hasattr(torch, "set_float32_matmul_precision"):
        torch.set_float32_matmul_precision("medium")


# ===================================================================
# Backend: Jina CLIP v2 (transformers)
# ===================================================================

def _validate_jina_dim(model: torch.nn.Module, embedding_dim: Optional[int]) -> None:
    if embedding_dim is None:
        return
    supported = getattr(model.config, "matryoshka_dimensions", None) or MATRYOSHKA_DIMS
    if embedding_dim not in supported:
        raise ValueError(f"EMBEDDING_DIM={embedding_dim} not in {supported}")


def _truncate_jina(model: torch.nn.Module, features: torch.Tensor, embedding_dim: Optional[int]) -> torch.Tensor:
    if embedding_dim is None or embedding_dim >= JINA_FULL_DIM:
        return features
    _validate_jina_dim(model, embedding_dim)
    if hasattr(model, "_truncate_embeddings"):
        return model._truncate_embeddings(features, embedding_dim)
    return features[:, :embedding_dim]


def load_jina_clip(
    model_id: str = DEFAULT_JINA_MODEL_ID,
    device: Optional[torch.device] = None,
) -> Tuple[torch.nn.Module, object, torch.device]:
    from transformers import AutoModel, AutoProcessor, AutoTokenizer

    _patch_clip_loss_import()
    resolved_device = get_device(device)

    model = AutoModel.from_pretrained(model_id, trust_remote_code=True)
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*slow image processor.*")
        processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    processor.tokenizer = tokenizer

    model.to(resolved_device)
    model.eval()
    _setup_cuda_optimizations(resolved_device)

    return model, processor, resolved_device


# ===================================================================
# Backend: OpenCLIP (open_clip)
# ===================================================================

class OpenCLIPWrapper:
    """Thin wrapper so the rest of the codebase can call the same API."""

    _is_openclip = True

    def __init__(self, model, preprocess, tokenizer, arch: str):
        self.model = model
        self.preprocess = preprocess
        self.tokenizer = tokenizer
        self.arch = arch


def load_openclip(
    arch: str = DEFAULT_OPENCLIP_ARCH,
    pretrained: str = DEFAULT_OPENCLIP_PRETRAINED,
    device: Optional[torch.device] = None,
) -> Tuple[OpenCLIPWrapper, None, torch.device]:
    import open_clip

    resolved_device = get_device(device)
    model, _, preprocess = open_clip.create_model_and_transforms(arch, pretrained=pretrained)
    tokenizer = open_clip.get_tokenizer(arch)

    model.to(resolved_device)
    model.eval()
    _setup_cuda_optimizations(resolved_device)

    wrapper = OpenCLIPWrapper(model, preprocess, tokenizer, arch)
    return wrapper, None, resolved_device


# ===================================================================
# Unified load entry point
# ===================================================================

def load_model(
    model_id: str = DEFAULT_OPENCLIP_ARCH,
    pretrained: str = DEFAULT_OPENCLIP_PRETRAINED,
    device: Optional[torch.device] = None,
) -> Tuple[object, object, torch.device]:
    """Load either OpenCLIP or Jina CLIP based on model_id.

    - If model_id starts with 'jinaai/' → load Jina CLIP v2 via transformers.
    - Otherwise → load via open_clip (e.g. 'ViT-L-14', 'ViT-B-32').
    """
    if model_id.startswith("jinaai/"):
        return load_jina_clip(model_id=model_id, device=device)
    return load_openclip(arch=model_id, pretrained=pretrained, device=device)


def _is_openclip(model) -> bool:
    """Check that survives Streamlit module reloads (isinstance can fail)."""
    return getattr(model, "_is_openclip", False)


# ===================================================================
# Shared helpers
# ===================================================================

def _bytes_to_rgb_image(image_data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(image_data))
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image


def normalize_l2(features: torch.Tensor) -> torch.Tensor:
    return F.normalize(features, p=2, dim=-1)


# ===================================================================
# Embed images
# ===================================================================

def embed_images_bytes(
    model,
    processor,
    images_data: Sequence[bytes],
    device: torch.device,
    embedding_dim: Optional[int] = None,
    keep_on_device: bool = False,
) -> torch.Tensor:
    if not images_data:
        raise ValueError("images_data must not be empty")

    if _is_openclip(model):
        return _embed_images_openclip(model, images_data, device, keep_on_device)
    return _embed_images_jina(model, processor, images_data, device, embedding_dim, keep_on_device)


def _embed_images_openclip(
    wrapper: OpenCLIPWrapper,
    images_data: Sequence[bytes],
    device: torch.device,
    keep_on_device: bool = False,
) -> torch.Tensor:
    preprocess = wrapper.preprocess

    def _decode_and_preprocess(raw: bytes) -> torch.Tensor:
        img = _bytes_to_rgb_image(raw)
        return preprocess(img)

    with ThreadPoolExecutor(max_workers=_PREPROCESS_WORKERS) as pool:
        tensors = list(pool.map(_decode_and_preprocess, images_data))

    batch = torch.stack(tensors).to(device)
    with torch.inference_mode(), torch.amp.autocast(device_type=device.type, enabled=device.type == "cuda"):
        features = wrapper.model.encode_image(batch)
        features = features.float()
        if keep_on_device and device.type == "cuda":
            return features
        return features.cpu()


def _embed_images_jina(
    model: torch.nn.Module,
    processor,
    images_data: Sequence[bytes],
    device: torch.device,
    embedding_dim: Optional[int],
    keep_on_device: bool = False,
) -> torch.Tensor:
    with ThreadPoolExecutor(max_workers=_PREPROCESS_WORKERS) as pool:
        images = list(pool.map(_bytes_to_rgb_image, images_data))
    inputs = processor(images=images, return_tensors="pt", padding=True).to(device)
    with torch.inference_mode(), torch.amp.autocast(device_type=device.type, enabled=device.type == "cuda"):
        features = model.get_image_features(**inputs)
        features = _truncate_jina(model, features, embedding_dim)
        features = features.float()
        if keep_on_device and device.type == "cuda":
            return features
        return features.cpu()


def embed_image_bytes(
    model,
    processor,
    image_data: bytes,
    device: torch.device,
    embedding_dim: Optional[int] = None,
    keep_on_device: bool = False,
) -> torch.Tensor:
    return embed_images_bytes(
        model, processor, [image_data], device,
        embedding_dim=embedding_dim, keep_on_device=keep_on_device,
    )[0]


# ===================================================================
# Embed text
# ===================================================================

def embed_text(
    model,
    processor,
    text: str,
    device: torch.device,
    embedding_dim: Optional[int] = None,
) -> torch.Tensor:
    if _is_openclip(model):
        return _embed_text_openclip(model, text, device)
    return _embed_text_jina(model, processor, text, device, embedding_dim)


def _embed_text_openclip(
    wrapper: OpenCLIPWrapper,
    text: str,
    device: torch.device,
) -> torch.Tensor:
    tokens = wrapper.tokenizer([text]).to(device)
    with torch.inference_mode(), torch.amp.autocast(device_type=device.type, enabled=device.type == "cuda"):
        features = wrapper.model.encode_text(tokens)
        return features.float().cpu()


def _embed_text_jina(
    model: torch.nn.Module,
    processor,
    text: str,
    device: torch.device,
    embedding_dim: Optional[int],
) -> torch.Tensor:
    inputs = processor(text=text, return_tensors="pt", padding=True).to(device)
    with torch.inference_mode():
        features = model.get_text_features(**inputs)
        features = _truncate_jina(model, features, embedding_dim)
        return features.float().cpu()


def get_native_dim(model) -> int:
    """Return the native embedding dimension of the loaded model."""
    if _is_openclip(model):
        return OPENCLIP_DIM
    return JINA_FULL_DIM
