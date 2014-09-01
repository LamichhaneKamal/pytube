from __future__ import unicode_literals

from .exceptions import *
from .models import Video
from .utils import safe_filename
try:
    from urllib import urlencode
    from urllib2 import urlopen
    from urlparse import urlparse, parse_qs, unquote
except ImportError:
    from urllib.parse import urlencode, urlparse, parse_qs, unquote
    from urllib.request import urlopen

import re, json, subprocess

YT_BASE_URL = 'http://www.youtube.com/get_video_info'

#YouTube quality and codecs id map.
#source: http://en.wikipedia.org/wiki/YouTube#Quality_and_codecs
YT_ENCODING = {
    #Flash Video
    5: ["flv", "240p", "Sorenson H.263", "N/A", "0.25", "MP3", "64"],
    6: ["flv", "270p", "Sorenson H.263", "N/A", "0.8", "MP3", "64"],
    34: ["flv", "360p", "H.264", "Main", "0.5", "AAC", "128"],
    35: ["flv", "480p", "H.264", "Main", "0.8-1", "AAC", "128"],

    #3GP
    36: ["3gp", "240p", "MPEG-4 Visual", "Simple", "0.17", "AAC", "38"],
    13: ["3gp", "N/A", "MPEG-4 Visual", "N/A", "0.5", "AAC", "N/A"],
    17: ["3gp", "144p", "MPEG-4 Visual", "Simple", "0.05", "AAC", "24"],

    #MPEG-4
    18: ["mp4", "360p", "H.264", "Baseline", "0.5", "AAC", "96"],
    22: ["mp4", "720p", "H.264", "High", "2-2.9", "AAC", "192"],
    37: ["mp4", "1080p", "H.264", "High", "3-4.3", "AAC", "192"],
    38: ["mp4", "3072p", "H.264", "High", "3.5-5", "AAC", "192"],
    82: ["mp4", "360p", "H.264", "3D", "0.5", "AAC", "96"],
    83: ["mp4", "240p", "H.264", "3D", "0.5", "AAC", "96"],
    84: ["mp4", "720p", "H.264", "3D", "2-2.9", "AAC", "152"],
    85: ["mp4", "520p", "H.264", "3D", "2-2.9", "AAC", "152"],

    #WebM
    43: ["webm", "360p", "VP8", "N/A", "0.5", "Vorbis", "128"],
    44: ["webm", "480p", "VP8", "N/A", "1", "Vorbis", "128"],
    45: ["webm", "720p", "VP8", "N/A", "2", "Vorbis", "192"],
    46: ["webm", "1080p", "VP8", "N/A", "N/A", "Vorbis", "192"],
    100: ["webm", "360p", "VP8", "3D", "N/A", "Vorbis", "128"],
    101: ["webm", "360p", "VP8", "3D", "N/A", "Vorbis", "192"],
    102: ["webm", "720p", "VP8", "3D", "N/A", "Vorbis", "192"]
}

# The keys corresponding to the quality/codec map above.
YT_ENCODING_KEYS = (
    'extension', 'resolution', 'video_codec', 'profile', 'video_bitrate',
    'audio_codec', 'audio_bitrate'
)


class YouTube(object):
    _filename = None
    _fmt_values = []
    _video_url = None
    _js_code = False
    _precompiled = False
    title = None
    videos = []
    # fmt was an undocumented URL parameter that allowed selecting
    # YouTube quality mode without using player user interface.

    @property
    def url(self):
        """Exposes the video url."""
        return self._video_url

    @url.setter
    def url(self, url):
        """ Defines the URL of the YouTube video."""
        self._video_url = url
        #Reset the filename.
        self._filename = None
        #Get the video details.
        self._get_video_info()

    @property
    def filename(self):
        """
        Exposes the title of the video. If this is not set, one is
        generated based on the name of the video.
        """
        if not self._filename:
            self._filename = safe_filename(self.title)
        return self._filename

    @filename.setter
    def filename(self, filename):
        """ Defines the filename."""
        self._filename = filename
        if self.videos:
            for video in self.videos:
                video.filename = filename

    @property
    def video_id(self):
        """Gets the video ID extracted from the URL."""
        parts = urlparse(self._video_url)
        qs = getattr(parts, 'query', None)
        if qs:
            video_id = parse_qs(qs).get('v', None)
            if video_id:
                return video_id.pop()

    def get(self, extension=None, resolution=None):
        """
        Return a single video given an extention and resolution.

        Keyword arguments:
        extention -- The desired file extention (e.g.: mp4).
        resolution -- The desired video broadcasting standard.
        """
        result = []
        for v in self.videos:
            if extension and v.extension != extension:
                continue
            elif resolution and v.resolution != resolution:
                continue
            else:
                result.append(v)
        if not len(result):
            return
        elif len(result) is 1:
            return result[0]
        else:
            d = len(result)
            raise MultipleObjectsReturned("get() returned more than one "
                                          "object -- it returned {}!".format(d))

    def filter(self, extension=None, resolution=None):
        """
        Return a filtered list of videos given an extention and
        resolution criteria.

        Keyword arguments:
        extention -- The desired file extention (e.g.: mp4).
        resolution -- The desired video broadcasting standard.
        """
        results = []
        for v in self.videos:
            if extension and v.extension != extension:
                continue
            elif resolution and v.resolution != resolution:
                continue
            else:
                results.append(v)
        return results

    def _fetch(self, path, data):
        """
        Given a path, traverse the response for the desired data. (A
        modified ver. of my dictionary traverse method:
        https://gist.github.com/2009119)

        Keyword arguments:
        path -- A tuple representing a path to a node within a tree.
        data -- The data containing the tree.
        """
        elem = path[0]
        #Get first element in tuple, and check if it contains a list.
        if type(data) is list:
            # Pop it, and let's continue..
            return self._fetch(path, data.pop())
        #Parse the url encoded data
        data = parse_qs(data)
        #Get the element in our path
        data = data.get(elem, None)
        #Offset the tuple by 1.
        path = path[1::1]
        #Check if the path has reached the end OR the element return
        #nothing.
        if len(path) is 0 or data is None:
            if type(data) is list and len(data) is 1:
                data = data.pop()
            return data
        else:
            # Nope, let's keep diggin'
            return self._fetch(path, data)

    def _parse_stream_map(self, text):
        """
        Python's `parse_qs` can't properly decode the stream map
        containing video data so we use this instead.

        Keyword arguments:
        data -- The parsed response from YouTube.
        """
        videoinfo = {
            "itag": [],
            "url": [],
            "quality": [],
            "fallback_host": [],
            "s": [],
            "type": []
        }

        # Split individual videos
        videos = text.split(",")
        # Unquote the characters and split to parameters
        videos = [video.split("&") for video in videos]

        for video in videos:
            for kv in video:
                key, value = kv.split("=")
                videoinfo.get(key, []).append(unquote(value))

        return videoinfo

    def _findBetween(self, s, first, last):
        try:
            start = s.index(first) + len(first)
            end = s.index( last, start )
            return s[start:end]
        except ValueError:
            return ""

    def _get_video_info(self):
        """
        This is responsable for executing the request, extracting the
        necessary details, and populating the different video
        resolutions and formats into a list.
        """
        self.title = None
        self.videos = []

        response = urlopen(self.url)

        if response:
            content = response.read().decode("utf-8")
            try:
                player_conf = content[18 + content.find("ytplayer.config = "):]
                bracket_count = 0
                for i, char in enumerate(player_conf):
                    if char == "{":
                        bracket_count += 1
                    elif char == "}":
                        bracket_count -= 1
                        if bracket_count == 0:
                            break
                else:
                    raise YouTubeError("Cannot get JSON from HTML")
                
                data = json.loads(player_conf[:i+1])
            except Exception as e:
                raise YouTubeError("Cannot decode JSON: {0}".format(e))

            stream_map = self._parse_stream_map(data["args"]["url_encoded_fmt_stream_map"])

            self.title = data["args"]["title"]
            js_url = "http:" + data["assets"]["js"]
            video_urls = stream_map["url"]

            for i, url in enumerate(video_urls):
                try:
                    fmt, fmt_data = self._extract_fmt(url)
                except (TypeError, KeyError):
                    continue
                
                # If the signature must be ciphered...
                if "signature=" not in url:
                    signature = self._cipher(stream_map["s"][i], js_url)
                    url = "%s&signature=%s" % (url, signature)
                
                self.videos.append(Video(url, self.filename, **fmt_data))
                self._fmt_values.append(fmt)
            self.videos.sort()

    def _cipher(self, s, url):
        """
        Get the signature using the cipher implemented in the JavaScript code

        Keyword arguments:
        s -- Signature
        url -- url of JavaScript file
        """

        # Getting JS code (if hasn't downloaded yet)
        if not self._js_code:
            self._js_code = urlopen(url).read().decode() if not self._js_code else self._js_code

        codes = re.findall(r"function \S{2}\(\S{1}\)\{\S{1}=\S{1}\.split\(\"\"\)\;(.*?)\}", self._js_code)
        #print("Got %s codes..." % len(codes))
        for code in codes:
            try:
                #print("Trying: %s" % code)
                # Get the helper object that's used by the cipher code
                obj = re.findall(r"[a-zA-Z]{2}\.", code)[0][0:2]
                obj = re.findall(r"var " + obj + "=.*?};", self._js_code)[0]
                print("JS Object is %s" % obj)
                gfile = open("/tmp/debug.html", "w")
                gfile.write("Object is %s\n" % code[2:4])
                gfile.write(self._js_code)
                gfile.close()

                signature = "a='" + s + "'.split('');"
                # ^ Maybe figure out the name of the var instead of assuming 'a' (Which seems to always be true as of now)
                # Actually, on all 3 videos I've encountered the cipher function was exactly the same,
                # so maybe you don't need any kind of JS interpreter at all and just implement the
                # algorithm in python...

                # Wrap in function and evaluate using system's js interpreter (ie spidermonkey)
                code = obj[4:] + ' function getsig(){' + signature + code + '}; print(getsig());'
                #print("Compiling: '%s'" % code)
                gfile = open("/tmp/yt.js", "w") # Dump to file for easier debugging
                # Could pipe directly into process, would make it work on windows etc...
                gfile.write(code)
                gfile.close()
                ret = subprocess.check_output([ "js", "/tmp/yt.js" ], stderr=subprocess.STDOUT).strip().decode(encoding='UTF-8')
                if len(ret) < 10: # Better: check exit code
                    continue
                #print("Signature seems to be '%s'" % ret)
                return ret

            except Exception as e:
                print("Oopsie... ", format(e))

        raise CipherError("Couldn't cipher the signature. Maybe YouTube has changed the cipher algorithm. Notify this issue on GitHub: %s" % format(e))

    def _extract_fmt(self, text):
        """
        YouTube does not pass you a completely valid URLencoded form,
        I suspect this is suppose to act as a deterrent.. Nothing some
        regulular expressions couldn't handle.

        Keyword arguments:
        text -- The malformed data contained within each url node.
        """
        itag = re.findall('itag=(\d+)', text)
        if itag and len(itag) is 1:
            itag = int(itag[0])
            attr = YT_ENCODING.get(itag, None)
            if not attr:
                return itag, None
            return itag, dict(zip(YT_ENCODING_KEYS, attr))
