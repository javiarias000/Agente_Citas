#!/bin/bash
# Type checking script using mypy

set -e

echo "🔍 Running mypy type checks..."
mypy \
    --config-file=mypy.ini \
    --junit-xml=reports/mypy-junit.xml \
    --html=reports/mypy-html \
    --any-exprs-report=reports/mypy-any \
    2>&1 | tee reports/mypy.log

exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo "✅ Type checking passed!"
else
    echo "❌ Type checking failed with exit code $exit_code"
fi

exit $exit_code
