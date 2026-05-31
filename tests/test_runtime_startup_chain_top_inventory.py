from pathlib import Path
import ast


def _modules_from_source(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'MODULES':
                    return [item.value for item in node.value.elts if isinstance(item, ast.Constant)]
    raise AssertionError('MODULES not found')


def test_top_inventory_scope_patch_is_installed_before_progressive_coverage():
    modules = _modules_from_source(Path('app/services/runtime_startup_chain.py'))
    assert 'app.services.top_inventory_runtime_scope_patch' in modules
    assert modules.index('app.services.top_inventory_runtime_scope_patch') < modules.index('app.services.progressive_coverage_runtime_patch')


def test_main_publish_guard_remains_after_exact_offer_bridge():
    modules = _modules_from_source(Path('app/services/runtime_startup_chain.py'))
    assert modules.index('app.services.bzzoiro_exact_offer_bridge_patch') < modules.index('app.services.main_publish_strict_value_guard')
    assert modules.index('app.services.main_publish_strict_value_guard') < modules.index('app.services.candidate_factory_runtime_diagnostics')
