EHKAM dual verification password reset: Resend email + Authevo WhatsApp.
1. Upload all files from this ZIP to the root of the same GitHub repo, replacing matching files.
2. Railway variables: RESEND_API_KEY, RESEND_FROM_EMAIL, AUTHEVO_API_KEY.
3. Each employee must have a valid email AND mobile phone (+20 or +966) saved in their employee profile.
4. Flow: request -> email 6 digits -> WhatsApp 6 digits -> new password. Both validations happen on server.
5. Test using a dedicated non-production employee account; check bad/expired codes and correct sequential verification.
6. Existing SQLite DB is local to container. This prototype is not production ready without persistent storage, hardened default accounts, proper session persistence and security audit.
7. Never paste API keys into chat or commit them into GitHub.
