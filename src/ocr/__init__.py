"""OCR module — two sequential stages.

    preprocessor : submission file -> normalized page images
    extractor    : page images     -> ExtractedSubmission (text + diagram crops)

Layout detection is folded into the extractor: each page image is sent to a
cloud vision model (Gemini or Azure) in a single request that returns ordered
text blocks and diagram bounding boxes together.
"""
