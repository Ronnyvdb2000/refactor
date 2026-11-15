#!/usr/bin/env python3
"""Compatibility entrypoint; implementation lives in meta_models.a_par_model.

Retained for existing callers while workflows are migrated separately.
"""
from meta_models.a_par_model import main

if __name__ == "__main__":
    main()
