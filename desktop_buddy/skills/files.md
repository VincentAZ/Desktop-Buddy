Create, edit and organize files with precise scope and verification.

Use workspace-relative paths. List parent directories first. Reading outside the workspace needs explicit permission; credential and app-state paths are blocked.
For existing files, read and retain the sha256 before fs.write. Writes replace atomically and verify bytes. A precondition failure means read again; never force an overwrite blindly.
fs.move refuses to overwrite and supports regular files on the same filesystem. Create a destination directory before moving.
fs.delete is irreversible, restricted to one regular file, and always asks for confirmation. Avoid deletion when a non-overwriting move meets the user's goal.
For JSON/CSV/YAML use a reliable existing parser through an approved command when structural validation is needed; do not invent successful validation.
