"""One module per upstream source.

Each holds its own constants, URL builders, watermark helpers and dlt
resource. What stays in `ingest.pipeline` is coordination: which resources
exist, how they group for loading, and the pipeline object itself.
"""
