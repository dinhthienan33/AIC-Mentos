from pydantic import BaseModel, Field
from typing import List, Optional


class KeyframeResult(BaseModel):
    query : str = Field(..., description="Query")
    full_name: str = Field(..., description="Full name of the keyframe")
    keyframe_id: str = Field(..., description="Unique keyframe identifier")
    video_id: str = Field(..., description="Video identifier (e.g., L21_V001)")
    group_id: str = Field(..., description="Group identifier (e.g., L21)")
    keyframe_num: int = Field(..., description="Keyframe number within video")
    fps: float = Field(..., description="Video frames per second (from urls.csv)")
    timestamp: float = Field(..., description="Timestamp of the keyframe")
    confidence_score: float = Field(..., description="Search confidence score")
    image_url: Optional[str] = Field(None, description="Keyframe image URL (HF proxy or S3)")
    image_path: str = Field(..., description="Original image path")
    video_url : Optional[str] = Field(..., description="Youtube url take form metadata")
    thumbnail_url: Optional[str] = Field(None, description="Youtube thumbnail url take form metadata")

class DeniedImageItem(BaseModel):
    keyframe_id: Optional[str] = None
    video_id: Optional[str] = None
    group_id: Optional[str] = None
    keyframe_num: Optional[int] = None
    image_path: Optional[str] = None
    image_url: Optional[str] = None
    confidence_score: Optional[float] = None
    reason: str = Field(..., description="Reject reason: too_dark | too_blurry | too_small | load_failed")
    brightness: Optional[float] = None
    laplacian_var: Optional[float] = None


class DeniedImages(BaseModel):
    total: int = Field(0, description="Number of denied images")
    items: List[DeniedImageItem] = Field(default_factory=list, description="Denied images with reasons")


class TextSearchResponse(BaseModel):
    query: str = Field(..., description="Original search query")
    translated_query : str = Field(..., description="Translated query")
    total_results: int = Field(..., description="Total number of results found")
    processing_time: float = Field(..., description="Search processing time in seconds")
    results: List[KeyframeResult] = Field(..., description="List of keyframe results")
    model_used: str = Field(..., description="Model used for search")
    denied_images: DeniedImages = Field(
        default_factory=lambda: DeniedImages(total=0, items=[]),
        description="Images rejected by quality preprocessing, grouped with reasons",
    )

class ASRSegment(BaseModel):
    segment_id: int = Field(..., description="Segment identifier")
    start_time: float = Field(..., description="Start time of the segment in seconds")
    end_time: float = Field(..., description="End time of the segment in seconds")
    duration: float = Field(..., description="Duration of the segment in seconds")
    text: str = Field(..., description="ASR transcript text for this segment")
    video_name: str = Field(..., description="Video name this segment belongs to")
    video_url: str = Field(..., description="Video url")
    score: float = Field(..., description="Elasticsearch relevance score for this segment")
    fps: Optional[float] = Field(None, description="Video fps from urls.csv, when known")
    start_frame: Optional[int] = Field(None, description="Frame index at the segment start")

class ASRVideoResult(BaseModel):
    video_name: str = Field(..., description="Video name (e.g., K01_V023)")
    score: float = Field(..., description="Search confidence score")
    segments: List[ASRSegment] = Field(..., description="List of matching segments")

class ASRSearchResponse(BaseModel):
    query: str = Field(..., description="Original search query")
    model_used: str = Field(..., description="Model used for search")
    total_results: int = Field(..., description="Total number of matching ASR segments returned")
    processing_time: float = Field(..., description="Search processing time in seconds")
    index: Optional[str] = Field(None, description="Elasticsearch index name when ASR is served from ES")
    results: List[ASRVideoResult] = Field(..., description="List of video results with segments")


class OCRSearchHit(BaseModel):
    video_id: str
    group_id: str
    keyframe_num: int
    fps: float
    timestamp: float
    ocr_text: str
    confidence_score: float
    image_path: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    highlight: Optional[str] = None


class OCRSearchResponse(BaseModel):
    query: str
    total_results: int
    processing_time: float
    index: str
    results: List[OCRSearchHit]

class FilteredSearchResponse(BaseModel):
    filters_applied: dict = Field(..., description="Filters that were applied")
    total_results: int = Field(..., description="Total number of results after filtering")
    results : List[str] = Field(..., description="List of filtered keyframe results")
    fps: dict = Field(default_factory=dict, description="video_id -> fps for the result paths")

class GroupInfo(BaseModel):
    """Group information extracted from search results"""
    group_id: str = Field(..., description="Group identifier (e.g., L21)")
    group_name: Optional[str] = Field(None, description="Display name for the group")
    video_count: int = Field(..., description="Number of videos in this group")
    total_keyframes: int = Field(..., description="Total keyframes found in this group")
    avg_confidence: float = Field(..., description="Average confidence score for this group")
    sample_videos: List[str] = Field(..., description="Sample video IDs in this group")


class GroupExtractionResponse(BaseModel):
    """Response for group extraction API"""
    query: str = Field(..., description="Original search query")
    total_groups: int = Field(..., description="Total number of groups found")
    total_keyframes: int = Field(..., description="Total keyframes analyzed")
    processing_time: float = Field(..., description="Processing time in seconds")
    groups: List[GroupInfo] = Field(..., description="List of groups found")
    model_used: str = Field(..., description="Model used for search")


class ShotResponse(BaseModel):
    video_id: str = Field(..., description="Unique shot identifier")
    video_url : str = Field(..., description="Video url")
    thumbnail_url : str = Field(..., description="Thumbnail url")
    start_time: float = Field(..., description="Start time of the shot in seconds")
    end_time: float = Field(..., description="End time of the shot in seconds")
    duration: float = Field(..., description="Duration of the shot in seconds")
    keyframes: List[KeyframeResult] = Field(..., description="Keyframes within this shot")

# class VideosResponse(BaseModel):
#     video_id : str = Field(..., description="Video identifier")
#     video_url : str = Field(..., description="Video url")
#     thumbnail_url : str = Field(..., description="Thumbnail url")
#     shots_list : List[ShotResponse] = Field(..., description="List of shot information")

class TemporalSearchResponse(BaseModel):
    query: str = Field(..., description="Original search query")
    translated_query : str = Field(..., description="Translated query")
    video_list : List[ShotResponse] = Field(..., description="List of video information")



