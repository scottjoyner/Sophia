# Admin voice enrollment UI

Sophia's browser capture page should include an admin-only enrollment panel for adding reviewed Scott voice clips when speaker matching accuracy is poor.

Behavior:

1. Record or upload a reviewed Scott-only clip.
2. Open the admin enrollment panel on the capture page.
3. Enter the configured local maintenance key.
4. Submit the clip to the server enrollment route.
5. The server appends the sample to the existing owner voiceprint instead of replacing it.
6. The response appears in the latest-action panel.

The route must remain disabled by default and must require the configured owner user id and maintenance key before any clip is enrolled.