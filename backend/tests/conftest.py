import os
import tempfile

# Before app.config is imported: keep test jobs/slides out of backend/.data and never call real agents.
os.environ["OUTPUT_DIR"] = os.path.join(tempfile.mkdtemp(prefix="wok-test-"), "out")
os.environ["DEMO_MODE"] = "fixture"
