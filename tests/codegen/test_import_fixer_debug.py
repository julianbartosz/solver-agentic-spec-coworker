"""Debug test for import fixer issues in file mode."""

from integration_coworker.codegen.import_fixer import fix_imports_for_policy_mode

# Test code that mimics what the LLM generates in file mode
test_code = '''
from integration_framework.core.client import IntegrationHttpClient
from integration_framework.core.exceptions import IntegrationError

class AirportsClient(IntegrationHttpClient):
    def __init__(self, api_key: str = None):
        super().__init__("https://api.airports.com")
        self.api_key = api_key
'''

def test_import_fixer():
    fixed_code, fixes = fix_imports_for_policy_mode(
        test_code,
        policy_mode="inline",
        artifact_type="client",
    )

    print("Fixes applied:", len(fixes))
    for fix in fixes:
        print(f"  - {fix.reason}")

    print("\nFixed code:")
    print(fixed_code[:800])

    # Check for integration_framework
    if "integration_framework" in fixed_code:
        print("\n❌ FAILED: integration_framework still present!")
        return False
    else:
        print("\n✅ PASSED: integration_framework removed")
        return True

if __name__ == "__main__":
    success = test_import_fixer()
    exit(0 if success else 1)
