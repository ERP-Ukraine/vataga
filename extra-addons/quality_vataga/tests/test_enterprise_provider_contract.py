"""Source/MRO recognition only; these tests do not emulate Enterprise costing."""
import importlib.util
import linecache
from pathlib import Path
import unittest


path = Path(__file__).resolve().parents[1] / 'models/native_cost_provider.py'
spec = importlib.util.spec_from_file_location('integration_provider_contract', path)
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)


class TestEnterpriseProviderContract(unittest.TestCase):
    def chain(self, source=None, filename=None):
        class Core:
            def _cal_cost(self):
                raise AssertionError('Recognition must never invoke cost methods')
        source = source or ("class MrpProductionWorkcenterLine(Core):\n"
            "    def _cal_cost(self):\n"
            "        return super()._cal_cost() + sum(self.time_ids.mapped('total_cost'))\n")
        filename = filename or provider.ENTERPRISE_COST_FILE
        linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
        self.addCleanup(linecache.cache.pop, filename, None)
        namespace = {'Core': Core, '__name__': provider.ENTERPRISE_COST_MODULE}
        exec(compile(source, filename, 'exec'), namespace)
        enterprise = namespace['MrpProductionWorkcenterLine']
        class Native(enterprise):
            def _cal_cost(self):
                raise AssertionError('Recognition must never invoke cost methods')
        return Native, enterprise, Core

    def test_exact_source_file_and_three_definition_mro_are_recognized(self):
        native, enterprise, core = self.chain()
        self.assertIs(provider.reviewed_enterprise_cost(native(), native._cal_cost, core._cal_cost), enterprise._cal_cost)

    def test_even_one_source_byte_change_is_rejected(self):
        native, enterprise, core = self.chain("class MrpProductionWorkcenterLine(Core):\n"
            "    def _cal_cost(self):\n"
            "        return super()._cal_cost() + sum(self.time_ids.mapped('total_cost')) # changed\n")
        self.assertIsNone(provider.reviewed_enterprise_cost(native(), native._cal_cost, core._cal_cost))

    def test_same_names_and_source_from_unreviewed_file_are_rejected(self):
        native, enterprise, core = self.chain(filename='/unreviewed/mrp_workorder.py')
        self.assertIsNone(provider.reviewed_enterprise_cost(native(), native._cal_cost, core._cal_cost))

    def test_extra_shadowing_override_and_wrong_native_identity_are_rejected(self):
        native, enterprise, core = self.chain()
        class Unknown(native):
            def _cal_cost(self):
                return super()._cal_cost()
        self.assertIsNone(provider.reviewed_enterprise_cost(Unknown(), native._cal_cost, core._cal_cost))
        self.assertIsNone(provider.reviewed_enterprise_cost(native(), Unknown._cal_cost, core._cal_cost))

    def test_wrapper_and_misdeclared_owner_are_rejected(self):
        native, enterprise, core = self.chain()
        enterprise._cal_cost.__wrapped__ = core._cal_cost
        self.assertIsNone(provider.reviewed_enterprise_cost(native(), native._cal_cost, core._cal_cost))
        del enterprise._cal_cost.__wrapped__
        enterprise.__module__ = 'unknown.provider'
        self.assertIsNone(provider.reviewed_enterprise_cost(native(), native._cal_cost, core._cal_cost))

    def test_missing_enterprise_and_changed_core_identity_are_rejected(self):
        native, enterprise, core = self.chain()
        self.assertIsNone(provider.reviewed_enterprise_cost(core(), native._cal_cost, core._cal_cost))
        self.assertIsNone(provider.reviewed_enterprise_cost(native(), native._cal_cost, lambda: None))
