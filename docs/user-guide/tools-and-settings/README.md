# Tools and Settings Panel (issue #836)

Last updated: 2026-08-31

The top bar used to carry three separate entry points -- a wrench for tools and
integrations, a gear for settings, and a sun/moon for the theme. They are now
one button, **Tools and Settings** (wrench icon), that opens a single tabbed
panel.

![Header with the single Tools and Settings button](screenshots/836-01-header.png)

## Tabs

| Tab | What it holds | Shown when |
| --- | ------------- | ---------- |
| Tools & Integrations | The former tools panel: installed MCP servers, tool and prompt selection, marketplace link | `FEATURE_TOOLS_ENABLED` |
| Prompts | Your custom prompt library ([details](../custom-prompts/README.md)) | `FEATURE_CUSTOM_PROMPTS_ENABLED` |
| General | LLM temperature, agent iterations, tool approval, compact messages, debug, Globus, and the light/dark toggle | always |
| User Info | Reserved stub (issue #595) | always |
| Admin | The most-used admin controls, plus a link to the full dashboard | user is in an admin group |

The panel opens on **Tools & Integrations** when that feature is on, and
remembers the tab you were last on for the rest of the session.

![Tools and Integrations tab](screenshots/836-02-tools-tab.png)

Tool selections are still staged: change what is enabled, then **Save Changes**.
Switching tabs keeps pending selections, and closing the panel with unsaved
selections still raises the save/discard prompt.

## Light and dark mode

The theme toggle moved off the top bar into **General → Appearance**. The
choice is still saved per browser.

![General tab with the Appearance toggle](screenshots/836-03-general-tab.png)

## Admin tab

Admins get the three most-used dashboard cards inline -- **MCP Configuration &
Controls**, **Banner Messages**, and **User Feedback** -- fully featured, not
read-only summaries. Everything else (logs, telemetry, config viewer, MCP
server manager, help content) stays on the full dashboard, one click away via
**Full Admin Page**.

![Admin quick controls tab](screenshots/836-04-admin-tab.png)

## Finding your custom prompts quickly

The prompt picker under the chat input now has a **New system prompt** button
and a pencil next to each of your saved prompts.

![Prompt picker with edit and create buttons](screenshots/836-05-prompt-selector.png)

Either one opens the Tools and Settings panel on the **Prompts** tab with the
editor already open on that prompt.

![Prompt editor opened from the picker](screenshots/836-06-prompt-editor.png)

## Chat-bar controls (UX review follow-up)

A UX review on PR #839 asked for the day-to-day controls to sit where the work
happens instead of in the top bar. The strip under the message box now carries
the model, the tools, and the prompts, in that order.

### Model

The model picker moved out of the top bar. It behaves exactly as it did there
-- vision/tools capability icons, the expandable model card, and the per-model
API key button -- but the menu opens upward.

### Tools

**Tools** opens a searchable menu of every available tool, grouped by server,
each row an icon, the tool name, a one-line description, and an on/off toggle.
Small changes never need a trip into the full panel; the footer link opens
Tools and Settings for everything else.

### Prompts

The picker's section heading used to read "Custom Prompts" in the same weight
as the selectable rows below it. It now reads **ADD CUSTOM PROMPTS** as a
muted, uppercase, non-interactive label.

### Enabled data sources

Enabled datasets show as pills above the message box. Each pill removes its
dataset; past three, the rest collapse into a `+N more` summary that expands in
place. The **Data Sources:** label opens the picker.

## Data sources live with the search tool

Data sources are only ever consumed by search, so the picker is now part of the
**Tools & Integrations** tab rather than only behind its own top-bar drawer.

The left-hand **Sources** drawer is unchanged and still available; both render
the same `DataSourcesSelector` component. Dataset names wrap instead of being
clipped, so a long name (or one with a suffix appended) stays readable.

Data source changes apply as soon as you make them, unlike the tool and prompt
checkboxes below, which are staged until you press **Save Changes**. The
section says so above the picker, because **Discard Changes** reverts the
staged tool and prompt selections and leaves the data sources alone.

## Top bar sizing

The admin shield is gone -- admin controls are the Admin tab, and the full
dashboard is one click on from there -- and Help and Portal are icons rather
than icon-plus-word.

The left-hand button labels are now driven by the header's own measured width
rather than a viewport media query. The header sits beside the sidebar and the
canvas panel, so a viewport query kept labels at widths where they no longer
fit and dropped them at widths where they did; "New Chat" was the worst case.

| Header width | Result |
| ------------ | ------ |
| >= 1080px | Full desktop button cluster |
| >= 760px | Buttons keep their text labels |
| < 760px | Icon-only buttons, cluster collapses into the menu |

## How it works

| Layer | Component | Responsibility |
| ----- | --------- | -------------- |
| Shell | `App.jsx` | Owns panel open state, the tab to open on, and the prompt intent |
| Panel | `SettingsPanel` | Tab chrome; hosts the tools, prompts, general, user-info, and admin tabs |
| Tools | `ToolsPanel` (`embedded` prop) | Same component as before; renders bodyless-of-chrome inside the panel and stays mounted across tab switches |
| Admin | `admin/AdminQuickPanel` + `useAdminConfigActions` | The three cards, sharing notification/modal plumbing with `AdminDashboard` |
| Prompts | `PromptSelector` -> `utils/settingsPanelEvents` -> `PromptManager` | The picker asks for a tab and an edit/create intent via an `atlas:open-settings` window event |
| Chat bar | `ModelSelector`, `ToolSelector`, `EnabledDataSourcesIndicator` | Model, tool toggles, and dataset pills under the message box, rendered by `ChatArea` |
| Data sources | `DataSourcesSelector` | One picker rendered by both `RagPanel` (drawer) and `ToolsPanel` (inline, `dense`) |
