import io
import json
import threading
import unittest
import urllib.error
import urllib.request

from PIL import Image
import tifffile
from http.server import ThreadingHTTPServer
import server
import codex_assist


class QuietHandler(server.Handler):
    def log_message(self, *args):
        pass


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.advisor = server.ADVISOR
        server.ADVISOR = codex_assist.Advisor()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        server.ADVISOR.shutdown(); server.ADVISOR = self.advisor

    def post(self, route, data, binary=False):
        body = data if binary else json.dumps(data).encode()
        return urllib.request.urlopen(urllib.request.Request(self.base + route, data=body,
            headers={'X-Z30-Request': '1', 'X-Filename': 'test.png'}), timeout=20)

    def open_photo(self):
        b = io.BytesIO(); Image.new('RGB', (12, 8), (150, 100, 80)).save(b, 'PNG', icc_profile=server.ICC)
        with self.post('/api/open', b.getvalue(), True) as response:
            return json.load(response)['id']

    def test_actual_http_tiff16_export(self):
        key = self.open_photo()
        with self.post('/api/export', dict(id=key, format='TIFF16', settings=dict(rotation=1))) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/tiff')
            image = tifffile.imread(io.BytesIO(response.read()))
        self.assertEqual(str(image.dtype), 'uint16')
        self.assertEqual(image.shape, (12, 8, 3))
        self.post('/api/close', {'id': key}).close()

    def test_closed_photo_job_cannot_be_retrieved_as_success(self):
        key = self.open_photo()
        server.ADVISOR.jobs['job'] = dict(state='done', source_id=key, result={'settings': {}})
        self.post('/api/close', {'id': key}).close()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + '/api/codex/jobs/job')
        self.assertEqual(cm.exception.code, 410)
        cm.exception.close()
        self.assertNotIn('result', server.ADVISOR.get('job'))


if __name__ == '__main__':
    unittest.main()
