// k6 steady baseline traffic against Online Boutique frontend.
// Gives Prometheus a normal-behavior baseline for the anomaly models.
import http from "k6/http";
import { sleep, check } from "k6";

const BASE = __ENV.TARGET || "http://frontend.boutique.svc.cluster.local";

export const options = {
  scenarios: {
    steady: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || 10),
      duration: __ENV.DURATION || "30m",
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
