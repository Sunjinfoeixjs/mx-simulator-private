"""Atomic role-save tests; all files are isolated in temporary directories."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# 2026-10-23 复活：sim 挪进 tests/ 后，原来的 dirname(dirname(file))/sim 指向
# 不存在的 `服务端源码/sim`，import 直接失败。改成布局无关查找（与其余测试同款）。
_HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(_HERE, 'sim')              # sim 与 tests 同层（现布局）
if not os.path.isdir(SIM_DIR):                    # 兼容旧的「tests 与 sim 平级」
    SIM_DIR = os.path.join(os.path.dirname(_HERE), 'sim')
sys.path.insert(0, SIM_DIR)
import m4_server


def _saved_files(directory):
    """瀛樻。鐩綍閲岀殑**浜х墿鏂囦欢**锛堟帓闄よ法杩涚▼閿佺殑鏃佽矾鏂囦欢锛夈€?

    `save_role` 鐜板湪浼氬垱寤?`<path>.lock` 鍋氳法杩涚▼浜掓枼锛堣 m4_server._CrossProcessLock锛夛紝
    瀹冩槸甯搁┗鐨勩€佹晠鎰忎笉鍒狅紙姣忚繘绋嬫瘡娆￠兘閲嶅缓浼氬紩鍏ョ珵鎬侊級銆?
    鏈祴璇曠粍鍏冲績鐨勬槸銆屼复鏃舵枃浠舵湁娌℃湁娈嬬暀銆佹寮忔。鏄惁瀹屾暣銆嶏紝
    鎵€浠ユ妸 .lock 鎺掗櫎鎺夛紝鑰屼笉鏄妸閿佸姛鑳藉垽鎴愬け璐ャ€?
    """
    return sorted(f for f in os.listdir(directory) if not f.endswith('.lock'))


class SaveRoleTests(unittest.TestCase):
    def test_replaces_save_with_complete_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'role.json')
            self.assertTrue(m4_server.save_role({'revision': 1}, path))
            self.assertTrue(m4_server.save_role({'revision': 2}, path))

            with open(path, encoding='utf-8') as f:
                self.assertEqual(json.load(f), {'revision': 2})
            self.assertEqual(_saved_files(directory), ['role.json'])

    def test_failed_replace_keeps_previous_save_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'role.json')
            self.assertTrue(m4_server.save_role({'revision': 1}, path))

            with patch.object(m4_server.os, 'replace', side_effect=PermissionError):
                self.assertFalse(m4_server.save_role({'revision': 2}, path))

            with open(path, encoding='utf-8') as f:
                self.assertEqual(json.load(f), {'revision': 1})
            self.assertEqual(_saved_files(directory), ['role.json'])

    def test_serialization_error_keeps_previous_save_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'role.json')
            self.assertTrue(m4_server.save_role({'revision': 1}, path))

            self.assertFalse(m4_server.save_role({'invalid': object()}, path))

            with open(path, encoding='utf-8') as f:
                self.assertEqual(json.load(f), {'revision': 1})
            self.assertEqual(_saved_files(directory), ['role.json'])

    def test_load_rejects_non_object_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'role.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(['not', 'a', 'role'], f)

            self.assertIsNone(m4_server.load_role(path))

    def test_load_restores_integer_item_and_task_ids(self):
        """JSON 的对象键只能是字符串，读档必须还原成 int。

        ★ 这里断言的是「**键的类型**」，不是原始 dict 形状：读档会顺带做条目
          规范化（normalize_items / normalize_tasks 补 shape/key/pos/state 等默认值），
          所以不能再拿原字典整体相等来断言。真正要守住的是：
          `items.get(2001)` / `tasks.get(7)` 用 int 能查到——
          查不到就会表现为「重启后背包空、任务栏消失」，且不报错。
        """
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'role.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({
                    'items': {'2001': {'shape': 22100, 'qty': 2}},
                    'tasks': {'7': {'state': 1, 'progress': 1}},
                }, f)

            saved = m4_server.load_role(path)

            self.assertIn(2001, saved['items'])
            self.assertNotIn('2001', saved['items'])
            self.assertEqual(saved['items'][2001]['qty'], 2)
            self.assertEqual(saved['items'][2001]['shape'], 22100)
            self.assertIn(7, saved['tasks'])
            self.assertNotIn('7', saved['tasks'])
            self.assertEqual(saved['tasks'][7]['progress'], 1)
            self.assertEqual(saved['tasks'][7]['state'], 1)

    def test_load_rejects_non_object_state_containers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'role.json')
            for field in ('hero_res', 'items', 'tasks'):
                with self.subTest(field=field):
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump({field: []}, f)
                    self.assertIsNone(m4_server.load_role(path))

    def test_server_preserves_archive_with_invalid_item_id(self):
        with tempfile.TemporaryDirectory() as directory:
            save_dir = os.path.join(directory, 'save')
            log_dir = os.path.join(directory, 'logs')
            os.makedirs(save_dir)
            path = os.path.join(save_dir, 'role.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'items': {'not-an-id': {'qty': 1}}}, f)
            with open(path, 'rb') as f:
                original = f.read()

            server = m4_server.M4Server(
                savedir=save_dir, logdir=log_dir, enemies=1)
            try:
                self.assertTrue(server._save_blocked)
                server._persist()
                with open(path, 'rb') as f:
                    self.assertEqual(f.read(), original)
            finally:
                server._logf.close()

    def test_server_does_not_overwrite_unreadable_existing_save(self):
        with tempfile.TemporaryDirectory() as directory:
            save_dir = os.path.join(directory, 'save')
            log_dir = os.path.join(directory, 'logs')
            os.makedirs(save_dir)
            path = os.path.join(save_dir, 'role.json')
            original = b'{broken json'
            with open(path, 'wb') as f:
                f.write(original)

            server = m4_server.M4Server(
                savedir=save_dir, logdir=log_dir, enemies=1)
            try:
                self.assertTrue(server._save_blocked)
                server._persist()
                with open(path, 'rb') as f:
                    self.assertEqual(f.read(), original)
            finally:
                server._logf.close()

    def test_server_can_save_when_archive_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            save_dir = os.path.join(directory, 'save')
            server = m4_server.M4Server(
                savedir=save_dir, logdir=os.path.join(directory, 'logs'),
                enemies=1)
            try:
                self.assertFalse(server._save_blocked)
                server._persist()
                self.assertTrue(os.path.isfile(server.savepath))
            finally:
                server._logf.close()


if __name__ == '__main__':
    unittest.main()

