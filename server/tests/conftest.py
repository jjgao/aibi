import os

from hypothesis import settings

# CI machines are slower and noisier than a laptop: no per-example deadline, and print the blob
# that reproduces a failure.
settings.register_profile("ci", deadline=None, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
