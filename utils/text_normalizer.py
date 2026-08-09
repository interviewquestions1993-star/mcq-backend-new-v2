import re

def normalize_text(text: str) -> str:
    """
    Normalizes text before evaluation:
    - Trims whitespace
    - Converts multiple spaces to one
    - Normalizes newlines
    - Removes trailing punctuation
    - Converts smart quotes to normal quotes
    """
    if not text:
        return ""
        
    # Replace smart quotes with normal quotes
    text = text.replace('‘', "'").replace('’', "'")
    text = text.replace('“', '"').replace('”', '"')
    
    # Normalize newlines
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Replace multiple spaces/newlines with a single space
    text = re.sub(r'\s+', ' ', text)
    
    # Trim leading/trailing whitespace
    text = text.strip()
    
    # Remove trailing punctuation
    text = re.sub(r'[.,;:]$', '', text)
    
    return text.strip()
