EHKAM HR Portal - Resend email recovery update

Upload the files in this archive to the ROOT of your existing GitHub repository (not a subfolder).
This package starts from the previously provided WhatsApp UI ZIP and the latest safe diagnostic server.py.

Railway variables:
RESEND_API_KEY = your private re_... key
RESEND_FROM_EMAIL = info@ehkambusiness.com

Resend domain ehkambusiness.com must show Verified.
A registered EMPLOYEE must have an email saved in their profile.
Only employees can use recovery. Codes are 6 digits, valid 10 minutes, max 5 verification attempts per code.
Resend API is called from the server only. Do not share the API key or put it in client JS.

WARNING: existing prototype uses a local SQLite file and sample seeded user accounts.
Before production use, configure durable private storage, rotate/remove demo accounts, and harden sessions.
Do not remove old provider variables until end-to-end email flow is tested.

Visual design: six separate slots animated on entry, white card, circular verification spinner.
