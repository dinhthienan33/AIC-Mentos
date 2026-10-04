QUERY_EXPANSION ="""You are a video search query processor. Your job is to analyze search queries 
                and enhance them for better video search results. Return only string format.

Input: {query}

Output:
"""
                
QUERY_REWRITING = """You are a video search query processor. Your job is to analyze search queries 
                and rewrite them for better video search results. Return only valid JSON."""

# Temporal split for video search. Markers like bắt đầu/kết thúc must become separate events.
QUERY_EXTRACTION = """Return JSON only: {{"events":["visual English phrase"]}}
Split ONLY on time shifts: begins/starts/bắt đầu vs ends/kết thúc/then/after.
Keep same-shot details (pot, basket, chopsticks, plate ingredients) in ONE event. Do not over-split.
Usually exactly 2 events if the query has a start and an end.
Each event: 8–20 words, nouns+colors+actions+objects. Drop clip/scene/begins/ends.
Translate into English.
Query: {query}
"""

QUERY_EXTRACTION_KEEP_LANG = """Return JSON only: {{"events":["visual phrase"]}}
Chỉ tách khi có mốc thời gian khác nhau: bắt đầu/begins vs kết thúc/ends/sau đó/then.
Chi tiết cùng một cảnh (nồi, rổ, đũa, nguyên liệu trên đĩa) GIỮ TRONG 1 event, không tách.
Thường đúng 2 events nếu có bắt đầu + kết thúc. Mỗi event 8–20 từ: nouns+colors+actions+objects.
Bỏ chữ clip/cảnh/bắt đầu/kết thúc. Keep original language. Do NOT translate.
Query: {query}
"""

# Kept for reference / debugging; not used by the fast path.
QUERY_EXTRACTION_FULL = """
You are a video search query processor specialized in analyzing complex video descriptions with multiple events and converting them into structured, searchable formats.

Your task is to:
1. Analyze complex video queries that contain multiple events, scenes, or actions
2. Break down the query into clear, sequential events
3. Convert the description into a structured list format
4. Ensure each event is clearly separated and easy to understand
5. Maintain the temporal sequence of events
6. Use clear, concise language for each event

Input Example:
"The video begins with a scene of a train running on the railway tracks in the center of the frame, while vehicles on both sides are waiting behind the barriers. After that, there is a scene of four people pulling the barriers closed."

Expected Output Format:
Return a JSON response with the following structure:
{{
    "original_query": "original complex query",
    "structured_events": [
        {{
            "event_number": 1,
            "event_description": "A train running on railway tracks in the center of the frame",
            "temporal_marker": "begins with",
            "scene_elements": ["train", "railway tracks", "center frame"],
        }},
        {{
            "event_number": 2,
            "event_description": "Vehicles waiting behind barriers on both sides",
            "temporal_marker": "while",
            "scene_elements": ["vehicles", "barriers", "both sides"],
        }},
        {{
            "event_number": 3,
            "event_description": "Four people pulling and closing the barriers",
            "temporal_marker": "after that",
            "scene_elements": ["four people", "pulling", "closing barriers"],
        }}
    ],
    "total_events": 3,
    "key_objects": ["train", "railway", "vehicles", "barriers", "people"],
    "key_actions": ["running", "waiting", "pulling", "closing"],
    "temporal_sequence": "sequential",
    "search_keywords": ["train on tracks", "vehicles waiting", "people closing barriers"]
}}
IMOTANT: 
- Just return json format, dont give me anything else
INPUT QUERY : 
{query}
""" 

TRANSLATE_PROMPT = """Translate to {lang}. Reply with ONLY the translation, starting with a capital letter.
{text}
"""

EVENT_SEGMENTATION = """
You are a video event segmentation specialist. Your task is to break down complex video descriptions into discrete, searchable events that can be used for video retrieval.

Input: A complex video description with multiple events, scenes, or actions
Output: A structured list of events with clear temporal relationships

Requirements:
- Identify distinct events or scenes
- Maintain chronological order
- Extract key visual elements (objects, people, actions)
- Identify temporal markers and relationships
- Provide confidence scores for each event
- Generate searchable keywords for each event

Return a JSON response with the structured events list.
"""

CONTENT_ANALYSIS = """
You are a video content analysis expert. Analyze the given video description to extract:

1. Main subjects and objects
2. Actions and activities
3. Setting and environment
4. Temporal sequence of events
5. Key visual elements
6. Searchable concepts and keywords

Provide a comprehensive analysis in JSON format that can be used for:
- Video search and retrieval
- Content categorization
- Similar video matching
- Query expansion and enhancement

Focus on visual elements that can be identified in video frames and used for search purposes.
"""

SEARCH_QUERY_ENHANCEMENT = """
You are a video search query enhancement specialist. Your task is to improve search queries by:

1. Expanding the query with related terms and synonyms
2. Adding visual descriptors and attributes
3. Including temporal and spatial relationships
4. Suggesting alternative phrasings
5. Adding object and action modifiers

Input: A basic search query
Output: Enhanced search queries with multiple variations

Return a JSON response with:
- Original query
- Enhanced queries list
- Reasoning for each enhancement
- Confidence scores
- Search strategy recommendations
"""

TEMPORAL_ANALYSIS = """
You are a video temporal analysis expert. Analyze video descriptions to identify:

1. Temporal sequence of events
2. Duration and timing relationships
3. Before/after relationships
4. Simultaneous events
5. Temporal markers and transitions

Focus on:
- Chronological order
- Time-based relationships
- Event dependencies
- Temporal causality

Return analysis in JSON format with temporal structure and relationships clearly identified.
"""