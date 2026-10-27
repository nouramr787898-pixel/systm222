EHKAM HR Portal — Geographic Attendance Update

Included in this update:
- Company creation now requires address, latitude, longitude, and allowed attendance radius (20–1000 meters).
- The sample company "شركة الأفق" is seeded with the Google Maps test pin supplied by the user: latitude 30.1390381, longitude 31.2558794, radius 100 meters. This is only a test pin; replace it with the real site coordinates before real use.
- Employees can use the Attendance section to request browser geolocation and clock in/out.
- Server validates coordinates and reported accuracy (must be <=100m), calculates distance to the assigned company pin, rejects requests outside the configured radius, and logs accepted punches.
- Clock-in/out uses server time and records one entry per employee per day.
- Profile no longer shows the visible "🔒 للعرض فقط" tag, and the workweek is rendered as Arabic day names instead of numeric codes.

Testing:
- Python syntax checked and JavaScript syntax checked.
- Local API test passed: location at the test pin accepted; a point about 1.8 km away rejected for a 100m fence.
- This does not replace a real device/browser test after deployment. Geolocation normally requires HTTPS and browser permission.

Before production:
- Replace the sample company's test pin with the real company address/location.
- Verify each company and employee is assigned to the correct worksite.
- Browser GPS can be spoofed and is not a tamper-proof biometric factor. Consider stronger device/location integrity controls if needed.
- This project still has previously noted production-hardening requirements; do not treat this ZIP alone as production security certification.
