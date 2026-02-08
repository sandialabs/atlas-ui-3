# Configuring the Splash Screen

Last updated: 2026-01-19

The splash screen feature allows you to display important policies and information to users when they first access the application. This is commonly used for displaying cookie policies, acceptable use policies, and other legal or organizational information.

*   **Location**: Place your custom file at `config/splash-config.json`.
*   **Feature Flag**: Enable the splash screen by setting `FEATURE_SPLASH_SCREEN_ENABLED=true` in your `.env` file.

The splash screen supports two operational modes:

1.  **Accept Mode** (`require_accept: true`): Users must explicitly click "I Accept" to proceed. The close (X) button is hidden.
2.  **Dismiss Mode** (`require_accept: false`): Users can dismiss the screen by clicking "Close" or the X button in the header.

User dismissals are tracked in the browser's local storage and will not show again until the configured duration expires (default: 30 days).

## Example `splash-config.json`

```json
{
  "enabled": true,
  "title": "Important Policies and Information",
  "messages": [
    {
      "type": "heading",
      "content": "Cookie Policy"
    },
    {
      "type": "text",
      "content": "This application uses cookies to enhance your experience and maintain your session. By continuing to use this application, you consent to our use of cookies."
    },
    {
      "type": "heading",
      "content": "Acceptable Use Policy"
    },
    {
      "type": "text",
      "content": "This system is for authorized use only. Users must comply with all applicable policies and regulations. Unauthorized access or misuse of this system may result in disciplinary action and/or legal prosecution."
    }
  ],
  "dismissible": true,
  "require_accept": true,
  "dismiss_duration_days": 30,
  "accept_button_text": "I Accept",
  "dismiss_button_text": "Close",
  "show_on_every_visit": false
}
```

## Configuration Fields

*   **`enabled`**: (boolean) Whether the splash screen is shown. Must be `true` and `FEATURE_SPLASH_SCREEN_ENABLED=true` in `.env`.
*   **`title`**: (string) The title displayed at the top of the splash screen modal.
*   **`messages`**: (array) A list of message objects. Each message has a `type` (`"heading"` or `"text"`) and `content` (string).
*   **`dismissible`**: (boolean) Whether users can dismiss the splash screen.
*   **`require_accept`**: (boolean) If `true`, users must click the accept button. If `false`, users can dismiss casually.
*   **`dismiss_duration_days`**: (number) Number of days before showing the splash screen again after dismissal.
*   **`accept_button_text`**: (string) Text for the accept button (shown when `require_accept` is `true`).
*   **`dismiss_button_text`**: (string) Text for the dismiss button (shown when `require_accept` is `false`).
*   **`show_on_every_visit`**: (boolean) If `true`, the splash screen will show every time, ignoring dismissal tracking.
