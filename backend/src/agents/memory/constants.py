"""Shared constants for memory system."""

import re

# Regex pattern for stripping ephemeral upload blocks from messages
# Used to remove <uploaded_files> tags that are session-scoped and should not
# persist in long-term memory.
UPLOAD_BLOCK_RE = re.compile(r"<uploaded_files>[\s\S]*?</uploaded_files>\n*", re.IGNORECASE)
