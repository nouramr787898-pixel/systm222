"""Resolve Google Maps links without following arbitrary external redirects."""
import math
import re
import urllib.parse
import urllib.request

HOSTS={'maps.app.goo.gl','goo.gl','google.com','www.google.com','maps.google.com'}

class LocationError(ValueError):pass

def validate_url(value):
    value=str(value or '').strip()
    if len(value)>4096:raise LocationError('رابط Google Maps طويل جدًا')
    url=urllib.parse.urlsplit(value)
    if url.scheme!='https' or url.hostname not in HOSTS or url.username or url.password or url.port not in (None,443):
        raise LocationError('أدخل رابط Google Maps صالحًا يبدأ بـ https://')
    if url.hostname=='goo.gl' and not url.path.startswith('/maps/'):
        raise LocationError('أدخل رابط Google Maps صالحًا يبدأ بـ https://')
    return value

def coordinates(url):
    value=urllib.parse.unquote(url)
    # Place coordinates take precedence over the camera/viewport centre.
    match=re.search(r'!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)',value)
    if not match:
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        for key in ('query','q'):
            text=query.get(key,[''])[0]
            match=re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*',text)
            if match:break
    if not match:match=re.search(r'@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)',value)
    if not match:return None
    lat,lon=map(float,match.groups())
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:
        raise LocationError('إحداثيات رابط GPS غير صحيحة')
    return lat,lon

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def resolve(value):
    value=validate_url(value)
    point=coordinates(value)
    if point:return point
    try:
        opener=urllib.request.build_opener(SafeRedirect())
        request=urllib.request.Request(value,headers={'User-Agent':'Mozilla/5.0'})
        with opener.open(request,timeout=10) as response:
            final=validate_url(response.geturl())
            point=coordinates(final)
            if point:return point
            # A canonical link sometimes carries the pin when the final URL does not.
            body=response.read(512*1024).decode('utf-8',errors='replace')
        from html.parser import HTMLParser
        class CanonicalLinks(HTMLParser):
            def __init__(self):super().__init__();self.links=[]
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='link' and 'canonical' in attrs.get('rel','').split():self.links.append(attrs.get('href',''))
                if tag=='meta' and attrs.get('property')=='og:url':self.links.append(attrs.get('content',''))
        parser=CanonicalLinks();parser.feed(body)
        for candidate in parser.links:
            validate_url(candidate)
            point=coordinates(candidate)
            if point:return point
    except LocationError:raise
    except Exception as exc:
        raise LocationError('تعذر قراءة رابط Google Maps؛ افتحه وانسخ الرابط الكامل الذي يحتوي على إحداثيات الموقع') from exc
    raise LocationError('لم نجد إحداثيات دقيقة في الرابط؛ اختر دبوس الموقع وانسخ رابط Google Maps الكامل')
