import tiktoken

# Canonical tokenizer for gpt-4o and the gpt-5 family. Scrape-time token counts
# and extraction-time budget math must agree on the encoding, so both sides
# import from here.
TOKEN_ENCODING = tiktoken.get_encoding("o200k_base")
