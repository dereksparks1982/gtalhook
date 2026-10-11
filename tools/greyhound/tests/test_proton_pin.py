#!/usr/bin/env python3
"""Offline tests: no game, Steam or Proton prefix is modified."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / 'Greyhound-Proton-Test-v1.3.py'
spec = importlib.util.spec_from_file_location('greyhound_proton_test', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

VDF = '''"InstallConfigStore"
{
 "Software" {
  "Valve" {
   "Steam" {
    "CompatToolMapping" {
     "0" { "name" "proton_experimental" }
     "271590" { "name" "proton_experimental" "priority" "250" }
     "570" { "name" "proton_9" }
    }
   }
  }
 }
}
'''


class StableProtonPinTests(unittest.TestCase):
    def test_replaces_only_gta_mapping(self):
        changed = module.pin_stable_mapping(VDF)
        self.assertIn('"name"\t\t"proton_10"', changed)
        self.assertEqual(changed.count('"271590"'), 1)
        self.assertIn('"570" { "name" "proton_9" }', changed)
        self.assertIn('"0" { "name" "proton_experimental" }', changed)

    def test_is_idempotent(self):
        once = module.pin_stable_mapping(VDF)
        self.assertEqual(module.pin_stable_mapping(once), once)

    def test_adds_game_override_without_replacing_default(self):
        original = VDF.replace('"271590" { "name" "proton_experimental" "priority" "250" }', '')
        changed = module.pin_stable_mapping(original)
        self.assertIn('"271590"', changed)
        self.assertIn('"name"\t\t"proton_10"', changed)
        self.assertIn('"0" { "name" "proton_experimental" }', changed)

    def test_refuses_unknown_mapping_structure(self):
        with self.assertRaisesRegex(RuntimeError, 'CompatToolMapping'):
            module.pin_stable_mapping('"InstallConfigStore" {}')

    def test_refuses_malformed_braces(self):
        with self.assertRaisesRegex(RuntimeError, 'Malformed'):
            module.pin_stable_mapping('"CompatToolMapping" { "271590" {')


if __name__ == '__main__':
    unittest.main()
