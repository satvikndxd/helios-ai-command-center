def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """
    Split text into fixed-size character chunks with overlap.

    Phase 2 keeps this deliberately simple; later phases add structure-aware
    chunking (headings, paragraphs, code functions, tables).
    """
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be >= 0 and < size")

    text = text.strip()
    if not text:
        return []

    if len(text) <= size:
        return [text]

    step = size - overlap
    chunks: list[str] = []
    start = 0
    while start < len(text):
        piece = text[start : start + size]
        if piece.strip():
            chunks.append(piece)
        if start + size >= len(text):
            break
        start += step

    return chunks
