"""This file runs ONLY in a resource-limited job container."""
import importlib.util
import json
import os
import resource
import traceback

resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024, 4 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))


def main():
    try:
        with open('/input/request.json', encoding='utf-8') as f:
            request = json.load(f)
        spec = importlib.util.spec_from_file_location('submitted_task', '/input/task.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        function = getattr(module, request['function_name'])
        if not callable(function):
            raise TypeError('Requested name must be callable')
        result = function(request['data'])
        if not isinstance(result, list):
            response = {'status': 'invalid_result', 'error': 'Function must return a JSON-serializable list'}
        else:
            try:
                serialized = json.dumps(result, allow_nan=False, ensure_ascii=True, separators=(',', ':'))
                if len(serialized) > 4 * 1024 * 1024 - 1024:
                    raise ValueError('Output exceeds 4 MiB')
                response = {'status': 'succeeded', 'result': result}
            except (TypeError, ValueError, OverflowError) as exc:
                response = {'status': 'invalid_result', 'error': str(exc)}
    except BaseException:
        response = {'status': 'script_error', 'error': traceback.format_exc()[-32768:]}
    # Structured result has its own file; stdout/stderr are never interpreted as JSON results.
    with open('/output/result.tmp', 'x', encoding='utf-8') as f:
        json.dump(response, f, allow_nan=False, ensure_ascii=True, separators=(',', ':'))
    os.replace('/output/result.tmp', '/output/result.json')


if __name__ == '__main__':
    main()
