// k6 multi-regime traffic: idle → ramp → steady → spike → decay, repeating.
// Teaches the anomaly model that load VARIATION is normal, so only genuine
// faults (not traffic changes) score high. Used for model v2+.
import http from "k6/http";
import { sleep, check } from "k6";

const BASE = __ENV.TARGET || "http://frontend.boutique.svc.cluster.local";

export const options = {
  scenarios: {
    varied: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "10m", target: 2 },   // near-idle
        { duration: "15m", target: 12 },  // morning ramp
        { duration: "30m", target: 12 },  // steady business hours
        { duration: "5m", target: 25 },   // flash spike (still normal!)
        { duration: "10m", target: 25 },  // sustained peak
        { duration: "15m", target: 5 },   // wind down
        { duration: "20m", target: 5 },   // evening low
        { duration: "15m", target: 12 },  // second ramp
        { duration: "30m", target: 8 },   // long tail
      ], // total: 2h30m
    },
  },
};

const PRODUCTS = [
  "0PUK6V6EV0", "1YMWWN1N4O", "2ZYFJ3GM2N", "66VCHSJNUP",
  "6E92ZMYYFZ", "9SIQT8TOJO", "L9ECAV7KIM", "LS4PSXUNUM", "OLJCESPC7Z",
];

export default function () {
  const home = http.get(`${BASE}/`);
  check(home, { "home 200": (r) => r.status === 200 });
  sleep(Math.random() * 2);

  const p = PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)];
  http.get(`${BASE}/product/${p}`);
  sleep(Math.random() * 2);

  if (Math.random() < 0.3) {
    http.post(`${BASE}/cart`, { product_id: p, quantity: "1" });
    sleep(1);
    http.get(`${BASE}/cart`);
  }
  sleep(Math.random() * 3);
}
