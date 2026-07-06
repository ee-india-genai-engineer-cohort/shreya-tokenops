"""Demo prompts and system contexts for the four host_app endpoints.

Consumed by:
  - host_app.main when an endpoint receives a request (SYSTEM_PROMPTS provide
    the role context wrapped around user input before forwarding to the proxy)
  - scripts/seed_demo_traffic.py to generate varied demo traffic (PROMPTS
    supply realistic per-endpoint examples)

Prompts were LLM-generated to avoid hand-crafted bias. 30 per endpoint,
120 total. No runtime rephrasing — the seed script picks from this pool
directly and lets the cache prove itself on natural variation.
"""


SYSTEM_PROMPTS: dict[str, str] = {
    "sql-analyst": (
        "You are a senior data analyst. The user asks a question about the company's "
        "data warehouse. Respond with the SQL query that answers it, then a one-line "
        "explanation. Assume standard tables: orders, customers, products, events. "
        "Be concise."
    ),
    "code-reviewer": (
        "You are a senior software engineer reviewing a code snippet. Identify bugs, "
        "security issues, performance problems, or style violations. Be specific and "
        "brief — three findings at most."
    ),
    "log-explainer": (
        "You are an on-call engineer. The user pastes a log line, stack trace, or "
        "error message. Explain what likely went wrong and the most probable cause "
        "in two or three sentences, then suggest the first thing to check."
    ),
    "doc-writer": (
        "You are a Python documentation writer. Given an undocumented function, "
        "produce a PEP 257-style docstring. Include Args, Returns, and Raises "
        "sections only when relevant. Keep it tight."
    ),
}


PROMPTS: dict[str, list[str]] = {
    "sql-analyst": [
        "What is the total revenue from orders in the last 30 days?",
        "Show me the top 10 customers by lifetime value",
        "How many new customers signed up each month this year?",
        "What is the average order value by product category?",
        "Which marketing campaigns have the highest conversion rate?",
        "Show me the customer retention rate by monthly cohorts for the past 12 months",
        "What is the churn rate for subscriptions by plan type?",
        "Compare revenue this quarter vs the same quarter last year",
        "What are the top 5 best-selling products in each category?",
        "Show me the funnel conversion rates from session to purchase",
        "Which customers have not made a purchase in the last 90 days?",
        "What is the average time between a customer's first and second order?",
        "Rank sales representatives by total revenue generated this quarter",
        "What percentage of customers who viewed a product actually purchased it?",
        "Show me the moving 7-day average of daily orders",
        "Which A/B test variant had better checkout completion rates?",
        "What is the predicted lifetime value of customers acquired through paid search?",
        "How many active subscriptions do we have by plan tier?",
        "Show me customers who downgraded their subscription in the last month",
        "What is the revenue breakdown by payment method?",
        "Which products are frequently purchased together?",
        "What is the median order value by customer segment?",
        "Show me the daily active users trend for the past 60 days?",
        "Which campaign source has the lowest customer acquisition cost?",
        "What percentage of free trial users convert to paid subscriptions?",
        "Show me the month-over-month growth rate in new customers",
        "Which products have the highest return rate?",
        "What is the average session duration by traffic source?",
        "Identify customers in the top 10% of spending who have not ordered in 60 days",
        "How does the LTV to CAC ratio compare across different acquisition channels?",
    ],
    "code-reviewer": [
        "def get_user(user_id):\n    query = \"SELECT * FROM users WHERE id = '\" + user_id + \"'\"\n    cursor.execute(query)\n    return cursor.fetchone()",
        "def run_command(user_input):\n    os.system(\"grep \" + user_input + \" /var/log/app.log\")",
        "def read_file(filename):\n    path = \"/var/www/uploads/\" + filename\n    with open(path, 'r') as f:\n        return f.read()",
        "AWS_SECRET_KEY = \"AKIAIOSFODNN7EXAMPLE\"\nDB_PASSWORD = \"super_secret_password123\"\ndef connect():\n    return create_connection(DB_PASSWORD)",
        "def load_user_session(data):\n    return pickle.loads(base64.b64decode(data))",
        "def safe_write(filename, data):\n    if os.path.exists(filename):\n        with open(filename, 'w') as f:\n            f.write(data)",
        "def process_file(path):\n    f = open(path, 'r')\n    data = f.read()\n    return process(data)",
        "def render_comment(comment):\n    return \"<div class='comment'>\" + comment + \"</div>\"",
        "def load_config(config_str):\n    return yaml.load(config_str)",
        "def authenticate(username, password):\n    user = get_user(username)\n    if user['password'] == password:\n        return True\n    return False",
        "def calculate(expression):\n    return eval(expression)",
        "def get_items(items):\n    result = []\n    for i in range(1, len(items)):\n        result.append(items[i])\n    return result",
        "def calculate_total(prices):\n    total = 0\n    for price in prices:\n        total += int(price) * 100\n    return total",
        "def login(username, password):\n    try:\n        user = db.query(username, password)\n    except DatabaseError as e:\n        return f\"Login failed: {str(e)}\"",
        "def set_age(age_str):\n    user.age = int(age_str)\n    user.save()",
        "def validate_email(email):\n    pattern = r'^([a-zA-Z0-9]+)+@[a-zA-Z0-9]+\\.[a-zA-Z]+$'\n    return re.match(pattern, email)",
        "def get_document(user, doc_id):\n    return Document.objects.get(id=doc_id)",
        "def generate_token():\n    return str(random.randint(100000, 999999))",
        "def add_item(item, items=[]):\n    items.append(item)\n    return items",
        "def get_username(user):\n    return user.profile.name.upper()",
        "def run_script(script_name):\n    subprocess.call(f\"python scripts/{script_name}\", shell=True)",
        "def search_users(name):\n    query = f\"SELECT * FROM users WHERE name LIKE '%{name}%'\"\n    return db.execute(query).fetchall()",
        "def download_file(file_id):\n    filename = request.args.get('name')\n    return send_file(f'./downloads/../{filename}')",
        "API_KEY = \"sk-proj-abc123xyz789\"\ndef call_api(prompt):\n    return openai.complete(api_key=API_KEY, prompt=prompt)",
        "def restore_object(serialized_data):\n    return pickle.loads(serialized_data)",
        "def create_temp_file(name):\n    path = f\"/tmp/{name}\"\n    if not os.path.exists(path):\n        time.sleep(0.1)\n        open(path, 'w').write('data')",
        "def fetch_data(url):\n    conn = http.client.HTTPConnection(url)\n    conn.request('GET', '/')\n    return conn.getresponse().read()",
        "def show_error(user_input):\n    return f\"<script>alert('Error: {user_input}')</script>\"",
        "def parse_config(yaml_content):\n    config = yaml.load(yaml_content, Loader=yaml.Loader)\n    return config",
        "def check_password(input_pw, stored_pw):\n    return input_pw == stored_pw",
    ],
    "log-explainer": [
        "Traceback (most recent call last):\n  File \"/app/services/user_service.py\", line 142, in get_user_profile\n    user = await self.db.fetch_one(query, user_id)\n  File \"/usr/local/lib/python3.11/site-packages/asyncpg/pool.py\", line 418, in fetch_one\n    return await self._execute(query, args, timeout=timeout)\nasyncpg.exceptions.ConnectionDoesNotExistError: connection was closed in the middle of operation",
        "Exception in thread \"main\" java.lang.OutOfMemoryError: GC overhead limit exceeded\n\tat java.util.Arrays.copyOf(Arrays.java:3210)\n\tat java.util.ArrayList.grow(ArrayList.java:265)\n\tat com.myapp.processing.BatchProcessor.processRecords(BatchProcessor.java:89)\n\tat com.myapp.Main.main(Main.java:42)",
        "panic: runtime error: invalid memory address or nil pointer dereference\n[signal SIGSEGV: segmentation violation code=0x1 addr=0x0 pc=0x4a2b3c]\n\ngoroutine 1 [running]:\nmain.(*Server).handleRequest(0x0, 0xc0001a4000)\n\t/go/src/app/server.go:127 +0x3c\nmain.main()\n\t/go/src/app/main.go:45 +0x1a5",
        "E0115 03:42:18.234567   1 pod_workers.go:191] Error syncing pod abc123-payment-service-7d8f9b6c4d-x2k9m (\"abc123-payment-service-7d8f9b6c4d-x2k9m_production(e4d5c6b7-a8b9-4c3d-2e1f-0a9b8c7d6e5f)\"), skipping: failed to \"StartContainer\" for \"payment-api\" with CrashLoopBackOff: \"back-off 5m0s restarting failed container\"",
        "ERROR 2024-01-15 08:23:45.789 [HikariPool-1 connection adder] com.zaxxer.hikari.pool.HikariPool - HikariPool-1 - Connection is not available, request timed out after 30000ms. Active: 10, Idle: 0, Waiting: 47",
        "curl: (60) SSL certificate problem: certificate has expired\nMore details here: https://curl.se/docs/sslcerts.html\n\ncurl failed to verify the legitimacy of the server and therefore could not establish a secure connection to it.",
        "redis.exceptions.ConnectionError: Error 111 connecting to redis-master.default.svc.cluster.local:6379. Connection refused.",
        "Warning  FailedScheduling  2m15s  default-scheduler  0/12 nodes are available: 4 Insufficient cpu, 8 Insufficient memory. preemption: 0/12 nodes are available: 12 No preemption victims found for incoming pod.",
        "pq: deadlock detected\nDETAIL: Process 18432 waits for ShareLock on transaction 847293; blocked by process 18456.\nProcess 18456 waits for ShareLock on transaction 847291; blocked by process 18432.\nHINT: See server log for query details.\nCONTEXT: while locking tuple (42,17) in relation \"orders\"",
        "HTTP/1.1 429 Too Many Requests\nRetry-After: 3600\nX-RateLimit-Limit: 1000\nX-RateLimit-Remaining: 0\nX-RateLimit-Reset: 1705312800\n\n{\"error\": \"rate_limit_exceeded\", \"message\": \"API rate limit exceeded for organization org_2x8kL9mN. Please retry after 2024-01-15T10:00:00Z\"}",
        "Jan 15 04:12:33 prod-web-03 kernel: Out of memory: Killed process 24601 (java) total-vm:8234512kB, anon-rss:7891024kB, file-rss:0kB, shmem-rss:0kB, UID:1000 pgtables:15892kB oom_score_adj:0",
        "Traceback (most recent call last):\n  File \"/app/auth/oauth.py\", line 89, in validate_token\n    decoded = jwt.decode(token, self.public_key, algorithms=[\"RS256\"])\n  File \"/usr/local/lib/python3.11/site-packages/jwt/api_jwt.py\", line 210, in decode\n    self._validate_claims(payload, merged_options, audience=audience, issuer=issuer, leeway=leeway)\njwt.exceptions.ExpiredSignatureError: Signature has expired",
        "2024-01-15T09:15:23.456Z ERROR [kafka-consumer-1] o.a.k.c.c.internals.ConsumerCoordinator - [Consumer clientId=consumer-payments-1, groupId=payment-processors] Offset commit failed on partition payments-events-7 at offset 1847293: The coordinator is not aware of this member.",
        "dial tcp 10.0.42.15:3306: connect: connection timed out\nError: failed to connect to database after 5 retries\n  at DatabasePool.connect (/app/node_modules/mysql2/lib/connection.js:234:17)\n  at processTicksAndRejections (internal/process/task_queues.js:95:5)",
        "WARN  [2024-01-15 11:23:45,678] org.eclipse.jetty.server.HttpChannel: /api/v2/users/bulk\norg.eclipse.jetty.io.EofException: Early EOF\n\tat org.eclipse.jetty.server.HttpInput.consumeAll(HttpInput.java:378)\n\tat org.eclipse.jetty.server.HttpChannel.handle(HttpChannel.java:525)",
        "level=error ts=2024-01-15T12:34:56.789Z caller=scrape.go:1145 component=\"scrape manager\" scrape_pool=kubernetes-pods target=http://10.244.3.42:8080/metrics msg=\"Scrape failed\" err=\"context deadline exceeded\"",
        "mongos | 2024-01-15T13:45:23.456+0000 E SHARDING [conn12847] Error writing to config server: FailedToSatisfyReadPreference: Could not find host matching read preference { mode: \"primary\" } for set config-rs",
        "AWS Error: RequestExpired - Request has expired. Timestamp: 2024-01-15T14:00:00Z. Signature Expiration: 2024-01-15T13:45:00Z. Current Time: 2024-01-15T14:01:23Z. Sync your system clock.",
        "Error: ENOSPC: no space left on device, write\n    at Object.writeSync (fs.js:568:3)\n    at writeFileSync (fs.js:1213:26)\n    at Logger.flush (/app/lib/logger.js:89:12)\nerrno: -28, syscall: 'write', code: 'ENOSPC', path: '/var/log/app/application.log'",
        "javax.net.ssl.SSLHandshakeException: PKIX path building failed: sun.security.provider.certpath.SunCertPathBuilderException: unable to find valid certification path to requested target\n\tat sun.security.ssl.Alert.createSSLException(Alert.java:131)\n\tat com.myapp.client.ApiClient.sendRequest(ApiClient.java:234)",
        "getaddrinfo ENOTFOUND api.stripe.com api.stripe.com:443\n    at GetAddrInfoReqWrap.onlookup [as oncomplete] (dns.js:66:26)\nError: DNS resolution failed for api.stripe.com after 3 attempts",
        "FATAL: password authentication failed for user \"app_readonly\"\nDETAIL: Connection matched pg_hba.conf line 94: \"host all all 10.0.0.0/8 md5\"\nLOG: could not receive data from client: Connection reset by peer",
        "W0115 15:23:45.678901       1 reflector.go:324] k8s.io/client-go/informers/factory.go:134: failed to list *v1.Secret: secrets is forbidden: User \"system:serviceaccount:default:my-app\" cannot list resource \"secrets\" in API group \"\" in the namespace \"kube-system\"",
        "grpc: received message larger than max (104857892 vs. 4194304)\n    at Object.exports.createStatusError (/app/node_modules/@grpc/grpc-js/build/src/call.js:31:26)\n    at Object.onReceiveStatus (/app/node_modules/@grpc/grpc-js/build/src/client.js:176:52)",
        "2024/01/15 16:45:23 http: proxy error: dial tcp [::1]:8001: connect: connection refused\nupstream: \"http://127.0.0.1:8001/api/v1/namespaces/production/services/backend-api:http/proxy/health\"\nrequest_id: \"a1b2c3d4-e5f6-7890-abcd-ef1234567890\"",
        "ElasticsearchException[Elasticsearch exception [type=cluster_block_exception, reason=index [logs-2024.01.15] blocked by: [TOO_MANY_REQUESTS/12/disk usage exceeded flood-stage watermark, index has read-only-allow-delete block];]]",
        "Traceback (most recent call last):\n  File \"/app/workers/email_worker.py\", line 67, in send_batch\n    response = self.ses_client.send_bulk_templated_email(**params)\n  File \"/usr/local/lib/python3.11/site-packages/botocore/client.py\", line 535, in _api_call\n    return self._make_api_call(operation_name, kwargs)\nbotocore.exceptions.ClientError: An error occurred (Throttling) when calling the SendBulkTemplatedEmail operation: Rate exceeded",
        "cassandra.cluster.NoHostAvailable: ('Unable to connect to any servers', {'10.0.1.15:9042': OperationTimedOut('errors=Timed out creating connection (5 seconds), last_host=10.0.1.15'), '10.0.1.16:9042': OperationTimedOut('errors=Timed out creating connection (5 seconds), last_host=10.0.1.16')})",
        "nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address already in use)\nnginx: [emerg] bind() to 0.0.0.0:443 failed (98: Address already in use)\nnginx: [emerg] still could not bind()\n2024/01/15 17:23:45 [emerg] 1#1: bind() to 0.0.0.0:80 failed (98: Address already in use)",
        "com.rabbitmq.client.ShutdownSignalException: connection error; protocol method: #method<connection.close>(reply-code=530, reply-text=NOT_ALLOWED - access to vhost 'production' refused for user 'app-worker', class-id=10, method-id=40)\n\tat com.rabbitmq.client.impl.AMQConnection.startExceptionHandler(AMQConnection.java:896)",
    ],
    "doc-writer": [
        "def reverse_words(sentence):\n    return ' '.join(sentence.split()[::-1])",
        "def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        yield a\n        a, b = b, a + b",
        "def read_csv_as_dicts(filepath):\n    import csv\n    with open(filepath, 'r') as f:\n        reader = csv.DictReader(f)\n        return list(reader)",
        "def memoize(func):\n    cache = {}\n    def wrapper(*args):\n        if args not in cache:\n            cache[args] = func(*args)\n        return cache[args]\n    return wrapper",
        "def flatten_nested_list(nested):\n    result = []\n    for item in nested:\n        if isinstance(item, list):\n            result.extend(flatten_nested_list(item))\n        else:\n            result.append(item)\n    return result",
        "def is_palindrome(s):\n    cleaned = ''.join(c.lower() for c in s if c.isalnum())\n    return cleaned == cleaned[::-1]",
        "def chunk_list(lst, size):\n    return [lst[i:i + size] for i in range(0, len(lst), size)]",
        "def retry(max_attempts=3, delay=1):\n    import time\n    def decorator(func):\n        def wrapper(*args, **kwargs):\n            for attempt in range(max_attempts):\n                try:\n                    return func(*args, **kwargs)\n                except Exception as e:\n                    if attempt == max_attempts - 1:\n                        raise\n                    time.sleep(delay)\n        return wrapper\n    return decorator",
        "def merge_dicts(*dicts):\n    result = {}\n    for d in dicts:\n        result.update(d)\n    return result",
        "def calculate_age(birthdate):\n    from datetime import date\n    today = date.today()\n    age = today.year - birthdate.year\n    if (today.month, today.day) < (birthdate.month, birthdate.day):\n        age -= 1\n    return age",
        "async def fetch_json(url):\n    import aiohttp\n    async with aiohttp.ClientSession() as session:\n        async with session.get(url) as response:\n            return await response.json()",
        "def find_duplicates(lst):\n    seen = set()\n    duplicates = set()\n    for item in lst:\n        if item in seen:\n            duplicates.add(item)\n        seen.add(item)\n    return list(duplicates)",
        "def deep_copy_dict(d):\n    import json\n    return json.loads(json.dumps(d))",
        "class Singleton:\n    _instance = None\n    def __new__(cls):\n        if cls._instance is None:\n            cls._instance = super().__new__(cls)\n        return cls._instance",
        "def binary_search(sorted_list, target):\n    left, right = 0, len(sorted_list) - 1\n    while left <= right:\n        mid = (left + right) // 2\n        if sorted_list[mid] == target:\n            return mid\n        elif sorted_list[mid] < target:\n            left = mid + 1\n        else:\n            right = mid - 1\n    return -1",
        "def snake_to_camel(snake_str):\n    components = snake_str.split('_')\n    return components[0] + ''.join(x.title() for x in components[1:])",
        "def safe_divide(a, b, default=None):\n    try:\n        return a / b\n    except ZeroDivisionError:\n        return default",
        "def tail_file(filepath, n=10):\n    with open(filepath, 'r') as f:\n        lines = f.readlines()\n        return lines[-n:]",
        "def validate_email(email):\n    import re\n    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'\n    return bool(re.match(pattern, email))",
        "def lru_cache_decorator(maxsize=128):\n    from collections import OrderedDict\n    def decorator(func):\n        cache = OrderedDict()\n        def wrapper(*args):\n            if args in cache:\n                cache.move_to_end(args)\n                return cache[args]\n            result = func(*args)\n            cache[args] = result\n            if len(cache) > maxsize:\n                cache.popitem(last=False)\n            return result\n        return wrapper\n    return decorator",
        "def running_average():\n    total = 0\n    count = 0\n    while True:\n        value = yield total / count if count else 0\n        total += value\n        count += 1",
        "def get_nested_value(data, keys, default=None):\n    for key in keys:\n        try:\n            data = data[key]\n        except (KeyError, IndexError, TypeError):\n            return default\n    return data",
        "class Timer:\n    def __init__(self):\n        self.elapsed = 0\n    def __enter__(self):\n        import time\n        self.start = time.perf_counter()\n        return self\n    def __exit__(self, *args):\n        import time\n        self.elapsed = time.perf_counter() - self.start",
        "def levenshtein_distance(s1, s2):\n    if len(s1) < len(s2):\n        return levenshtein_distance(s2, s1)\n    if len(s2) == 0:\n        return len(s1)\n    prev_row = range(len(s2) + 1)\n    for i, c1 in enumerate(s1):\n        curr_row = [i + 1]\n        for j, c2 in enumerate(s2):\n            insertions = prev_row[j + 1] + 1\n            deletions = curr_row[j] + 1\n            substitutions = prev_row[j] + (c1 != c2)\n            curr_row.append(min(insertions, deletions, substitutions))\n        prev_row = curr_row\n    return prev_row[-1]",
        "def watch_directory(path, callback, interval=1):\n    import os\n    import time\n    seen = {}\n    while True:\n        for filename in os.listdir(path):\n            filepath = os.path.join(path, filename)\n            mtime = os.path.getmtime(filepath)\n            if filepath not in seen or seen[filepath] != mtime:\n                seen[filepath] = mtime\n                callback(filepath)\n        time.sleep(interval)",
        "def parse_query_string(query):\n    from urllib.parse import parse_qs\n    return {k: v[0] if len(v) == 1 else v for k, v in parse_qs(query).items()}",
        "def exponential_backoff(attempt, base=2, max_delay=60):\n    import random\n    delay = min(base ** attempt + random.uniform(0, 1), max_delay)\n    return delay",
        "def group_by(iterable, key_func):\n    result = {}\n    for item in iterable:\n        key = key_func(item)\n        if key not in result:\n            result[key] = []\n        result[key].append(item)\n    return result",
        "def create_tcp_server(host, port, handler):\n    import socket\n    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:\n        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n        s.bind((host, port))\n        s.listen()\n        while True:\n            conn, addr = s.accept()\n            with conn:\n                data = conn.recv(1024)\n                response = handler(data, addr)\n                conn.sendall(response)",
        "def datetime_range(start, end, delta):\n    current = start\n    while current < end:\n        yield current\n        current += delta",
    ],
}
