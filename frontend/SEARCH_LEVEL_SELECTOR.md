# Search Level Selector

The Search Level Selector allows users to choose the scope of their search across different hierarchical levels:

## Levels

### 1. All
- Searches across all available data
- Uses the batch selection from the search form
- Default level when the interface loads

### 2. Batch
- Searches within a specific batch
- Batch 1: Groups L21-L30 (10 groups)
- Batch 2: Groups K01-K20 (20 groups)
- Requires selecting a specific batch before searching

### 3. Group
- Searches within a specific group within a batch
- Shows all groups available in the selected batch
- Displays file count for each group
- Requires selecting both batch and group before searching

### 4. Video
- Searches within a specific video within a group
- Shows all videos available in the selected group
- Videos are named as `{GROUP}_V{NNN}` (e.g., L21_V001, K01_V015)
- Requires selecting batch, group, and video before searching

## Usage

1. **Select Search Level**: Click on one of the four level buttons (All, Batch, Group, Video)
2. **Make Selections**: 
   - For Batch level: Select which batch to search
   - For Group level: Select batch first, then select group
   - For Video level: Select batch, then group, then video
3. **Search**: Enter your query and click search

## Data Structure

Based on the notes2.txt file structure:

### Batch 1 (L21-L30)
- L21: 29 files
- L22: 31 files
- L23: 25 files
- L24: 43 files
- L25: 88 files
- L26: 474 files
- L27: 16 files
- L28: 24 files
- L29: 23 files
- L30: 96 files

### Batch 2 (K01-K20)
- K01: 31 files
- K02: 31 files
- K03: 29 files
- K04: 30 files
- K05: 34 files
- K06: 31 files
- K07: 31 files
- K08: 30 files
- K09: 28 files
- K10: 28 files
- K11: 31 files
- K12: 31 files
- K13: 30 files
- K14: 30 files
- K15: 31 files
- K16: 32 files
- K17: 30 files
- K18: 27 files
- K19: 31 files
- K20: 31 files

## Features

- **Hierarchical Navigation**: Each level builds upon the previous selection
- **Visual Feedback**: Current selection is clearly displayed
- **Validation**: Prevents searching without proper selections
- **Responsive Design**: Works on different screen sizes
- **Search Scope Summary**: Shows the complete search path when not using "All" level

## Integration

The Search Level Selector integrates with the existing search functionality:
- Search parameters are automatically included in API calls
- Batch selection is synchronized with the search form
- Error messages guide users to make proper selections
- All existing search features (filtering, temporal search, etc.) work with the new level selector
