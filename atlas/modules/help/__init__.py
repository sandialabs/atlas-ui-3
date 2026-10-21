"""Help content sources: local Markdown, remotely hosted Markdown, external link.

The Help page has three sources, selected by ``HELP_DISPLAY_MODE`` (issue
#1107):

* ``local`` -- the Markdown that ships with Atlas or the operator's override.
  This is the pre-#1107 behavior and remains the default.
* ``remote_markdown`` -- Markdown fetched server-side from
  ``HELP_CONTENT_URL`` and rendered in the same Help page.
* ``external_link`` -- the Help button opens ``HELP_CONTENT_URL`` in a new
  browser tab; nothing is fetched and nothing is rendered in Atlas.

See :mod:`atlas.modules.help.help_config` for the mode resolution and
:mod:`atlas.modules.help.remote_content` for the fetching and caching.
"""
