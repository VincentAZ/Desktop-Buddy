# Reliable connected-app lookup

Desktop Buddy 0.4.16 keeps connected-app tool discovery small enough to read, even when a service such as GitHub advertises many tools with large icons or other display metadata.

- Discovery returns each tool's complete name, description and input schema. Optional titles and annotations are retained; icons, output schemas and vendor display metadata are omitted.
- Each page is limited to 24,000 serialized JSON bytes and at most 20 tools. A page can therefore contain fewer than 20 tools. Continue using the returned `next_offset`; `total` counts every matching tool, not just the current page.
- The optional `limit` setting accepts 1–20 tools per page (20 by default). The query and offset continue to work as before.
- Input schemas are never shortened to fit. If a single tool's description and input schema exceed the page budget, discovery reports an explicit read error rather than offering a partial schema. Buddy must not invoke that unread tool.
- Existing approvals, connected-app routing, failed-source change pauses and credential handling are unchanged.

Regression tests cover oversized icons and display metadata, byte-bounded pagination without missing or repeated tools, complete schema preservation, Unicode escaping, explicit oversized-schema errors, and the discovery entry point.

This change does not alter GNOME Text Editor's separate 2 MiB preview limit.
