// A bounded control-plane test: quotas and backpressure against mock jobs, with no GPU and no spending.
// Run through make test-load. It proves the limits hold. It says nothing about production scale.
import { check, sleep } from "k6";
import http from "k6/http";
import { Counter, Rate } from "k6/metrics";

const BASE = __ENV.MM_BASE_URL || "http://host.docker.internal:8088";
const COOKIE = __ENV.MM_SESSION_COOKIE;
const CSRF = __ENV.MM_CSRF_TOKEN;
const PROJECT = __ENV.MM_PROJECT_ID;
const REQUEST = __ENV.MM_GENERATE_BODY;

const accepted = new Counter("mm_submissions_accepted");
const limited = new Counter("mm_submissions_rate_limited");
const unexpected = new Rate("mm_unexpected_response");

export const options = {
  scenarios: {
    readers: { executor: "constant-vus", vus: 10, duration: "60s", exec: "read" },
    submitters: { executor: "constant-vus", vus: 5, duration: "60s", exec: "submit" },
  },
  thresholds: {
    mm_unexpected_response: ["rate==0"],
    "http_req_duration{kind:read}": ["p(95)<500"],
  },
};

const session = { headers: { Cookie: `SESSION=${COOKIE}; XSRF-TOKEN=${CSRF}`, "X-XSRF-TOKEN": CSRF } };

export function read() {
  const response = http.get(`${BASE}/api/v1/projects/${PROJECT}/jobs`, { ...session, tags: { kind: "read" } });
  unexpected.add(response.status !== 200);
  check(response, { "listing works under load": (r) => r.status === 200 });
  sleep(1);
}

export function submit() {
  const params = {
    headers: {
      ...session.headers,
      "Content-Type": "application/json",
      "Idempotency-Key": `${__VU}-${__ITER}-${Date.now()}`,
    },
    tags: { kind: "submit" },
  };
  const response = http.post(`${BASE}/api/v1/projects/${PROJECT}/jobs`, REQUEST, params);
  if (response.status === 201 || response.status === 200) {
    accepted.add(1);
  } else if (response.status === 429) {
    // The point of this test: limits answering clearly rather than the service falling over.
    limited.add(1);
  } else {
    unexpected.add(true);
    console.error(`unexpected ${response.status}`);
    return;
  }
  unexpected.add(false);
  sleep(1);
}
