import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import app_runtime
import server


class RuntimeTests(unittest.TestCase):
    def test_source_data_and_resources_do_not_depend_on_cwd(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, 'frozen', False, create=True):
            self.assertEqual(app_runtime.data_root(), app_runtime.resource_root()/'.data')
            self.assertTrue((app_runtime.resource_root()/'web'/'index.html').is_file())

    def test_frozen_data_survives_outside_bundle_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with patch.dict(os.environ, {'LOCALAPPDATA':str(base)}, clear=True), patch.object(sys,'frozen',True,create=True):
                self.assertEqual(app_runtime.data_root(),base/'Z30 Studio')
                with patch.dict(os.environ, {'Z30_DATA_DIR':str(base/'独立 数据')}):
                    self.assertEqual(app_runtime.data_root(),(base/'独立 数据').resolve())

    def test_busy_port_falls_back_to_another_loopback_port(self):
        first=server.create_server(0)
        try:
            second=server.create_server(first.server_port)
            try:
                self.assertEqual(second.server_address[0],'127.0.0.1')
                self.assertNotEqual(first.server_port,second.server_port)
            finally:second.server_close()
        finally:first.server_close()

    @unittest.skipUnless(os.name=='nt','Windows named mutex')
    def test_windows_instance_lock_is_scoped_to_data_and_released(self):
        from desktop import InstanceLock
        with tempfile.TemporaryDirectory() as folder:
            first=InstanceLock(Path(folder))
            try:
                with self.assertRaises(FileExistsError):InstanceLock(Path(folder))
                other=InstanceLock(Path(folder)/'another');other.close()
            finally:first.close()
            reopened=InstanceLock(Path(folder));reopened.close()
