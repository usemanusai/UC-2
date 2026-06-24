# engine/kernel/math_engine/__init__.py
# Marks the math_engine folder as a Python package.

import site
# Enable user site packages in environments where it is disabled (e.g. USER_SITE ENABLE_USER_SITE=False)
site.ENABLE_USER_SITE = True
site.main()
