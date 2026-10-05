def task(data):
    import time
    for second in range(15):
        print(f'Working: {second + 1}/15', flush=True)
        time.sleep(1)
    return [item * 2 for item in data]
