EHKAM - Authevo 403 diagnostic update
Replace ONLY server.py in GitHub root. Do NOT upload secret keys.
After Railway ACTIVE, make one test attempt and inspect Deploy Logs for:
AUTHEVO API ERROR endpoint=/v1/otp/send status=403 code=...
Only symbolic error codes are logged, not raw body, numbers, OTP or keys.
This diagnoses the error; it does not guarantee WhatsApp delivery.
