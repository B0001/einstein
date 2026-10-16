"""Experimental agent stages: ideator -> novelty audit -> feasibility ->
codegen -> sandbox -> report, plus triage.

Moved out of the shipped `einstein` package (einstein-p03): none of these is
wired into the CLI, and there is no concrete `LLMClient` yet. Not included in
the wheel, so it can only be imported from a repo checkout.
"""
