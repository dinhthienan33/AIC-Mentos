from pydantic import BaseModel, Field
from typing import List, Optional


class FilterringObject(BaseModel):
    ocr_text: Optional[List[str]] = None
    od_text: Optional[List[str]] = None
    asr_text: Optional[List[str]] = None


class FilteringRequest(BaseModel):
    query: Optional[str] = None
    origin_paths: Optional[List[str]] = None
    filtering: FilterringObject
    top_k: int = Field(default=10, ge=1, le=500, description="Max results when searching without origin_paths")
    model_name: Optional[str] = None


class TextSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, description="Search query text")
    top_k: int = Field(default=10, ge=1, description="Number of results to return")
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum confidence score")
    model_name: str = Field(default='siglip2', description="Model to use for search")
    search_method: Optional[str] = Field(
        default=None,
        description="normal | hybrid | temporal | filtering | hierarchical (alias: hierachical)",
    )
    filtering: Optional[FilterringObject] = None
    exclude_groups: List[str] = None
    exclude_vid_id: List[str] = None
    include_groups: List[str] = None
    include_videos: List[str] = None
    index_files: Optional[List[str]] = Field(
        default=None,
        description="Legacy FAISS shard names (e.g. L30_V001.bin). Converted to include_videos.",
    )
    batch: Optional[int] = Field(
        default=None,
        description="Legacy AIC2025 scope: 1 keeps L* video ids, 2 keeps K*. Omit to search every group.",
    )
    translate: bool = Field(
        default=False,
        description=(
            "If true, LLM-translate the query to English before search. "
            "Temporal search always splits events via LLM; this flag only controls translation."
        ),
    )
    quality_filter: bool = Field(
        default=False,
        description=(
            "If true, download keyframes from S3 to reject blurry/dark frames "
            "(slower). Default false: only attach presigned image_url for viewing."
        ),
    )
    min_brightness: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=255.0,
        description="Override brightness threshold (0-255). Frames darker than this are denied.",
    )
    min_laplacian_var: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Override blur threshold (Laplacian variance). Lower = more blurry.",
    )

class ExcludeRequest(BaseModel):
    exclude_groups: List[str] = Field(default_factory=list, description="List of group IDs to exclude")
    exclude_vid_id: List[str] = Field(default_factory=list, description="List of video IDs to exclude")

class GroupExtractionRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, description="Search query text")
    top_k: int = Field(default=50, ge=1, le=1000, description="Number of results to analyze for groups")
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum confidence score")
    model_name: str = Field(default='siglip2', description="Model to use for search")


class FilteredSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, description="Search query text")
    top_k: int = Field(default=10, ge=1, le=500, description="Number of results to return")
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum confidence score")
    include_groups: List[str] = Field(default_factory=list, description="List of group IDs to include (e.g., ['L21', 'L22'])")
    include_videos: List[str] = Field(default_factory=list, description="List of video IDs to include (e.g., ['L21_V001', 'L22_V005'])")
    exclude_groups: List[str] = Field(default_factory=list, description="List of group IDs to exclude")
    model_name: str = Field(default='siglip2', description="Model to use for search")

class ShowImagesRequest(BaseModel):
    image_paths: List[str] = Field(..., description="List of image paths to display")
    container_name: str = Field(default='aic2026/keyframes', description="S3 keyframe prefix / bucket path")

class VideoExtractionRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, description="Search query text")
    top_k: int = Field(default=10, ge=1, le=500, description="Number of results to return")
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum confidence score")
    model_name: str = Field(default='siglip2', description="Model to use for search")

class ASRRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, description="Search query text")
    top_k: int = Field(
        default=10,
        ge=1,
        le=500,
        description="Number of matching ASR transcript segments to return (grouped by video in the response)",
    )
    model_name: str = Field(default='siglip2', description="Model to use for search")
    include_groups: Optional[List[str]] = Field(default=None, description="Restrict to groups, e.g. L21")
    include_videos: Optional[List[str]] = Field(default=None, description="Restrict to video ids")


class OCRRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, description="OCR text to search")
    top_k: int = Field(default=10, ge=1, le=500, description="Number of matching frames to return")
    include_groups: Optional[List[str]] = Field(default=None, description="Restrict to groups, e.g. L21")
    include_videos: Optional[List[str]] = Field(default=None, description="Restrict to video ids")
