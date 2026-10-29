import json
import sys
sys.path.insert(0, ".")
from app.main import app

out = sys.argv[1] if len(sys.argv) > 1 else "../docs/openapi.json"
json.dump(app.openapi(), open(out, "w"), indent=1)
print("wrote", out)
