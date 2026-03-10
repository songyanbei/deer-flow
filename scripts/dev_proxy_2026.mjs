import http from "node:http";

const port = 2026;

function resolveTarget(pathname) {
  if (pathname.startsWith("/api/langgraph/")) {
    return {
      hostname: "127.0.0.1",
      port: 2024,
      path: pathname.slice("/api/langgraph".length) || "/",
    };
  }

  if (
    pathname.startsWith("/api/") ||
    pathname === "/health" ||
    pathname === "/docs" ||
    pathname === "/redoc" ||
    pathname === "/openapi.json"
  ) {
    return {
      hostname: "127.0.0.1",
      port: 8001,
      path: pathname,
    };
  }

  return {
    hostname: "127.0.0.1",
    port: 3000,
    path: pathname,
  };
}

const server = http.createServer((req, res) => {
  const { hostname, port: targetPort, path } = resolveTarget(req.url || "/");
  const upstream = http.request(
    {
      hostname,
      port: targetPort,
      path,
      method: req.method,
      headers: {
        ...req.headers,
        host: `${hostname}:${targetPort}`,
        connection: "close",
      },
    },
    (upstreamRes) => {
      const headers = { ...upstreamRes.headers };
      delete headers["content-encoding"];
      delete headers["transfer-encoding"];
      headers.connection = "close";
      res.writeHead(upstreamRes.statusCode || 502, headers);
      upstreamRes.pipe(res);
    },
  );

  upstream.on("error", (error) => {
    const message = Buffer.from(String(error));
    res.writeHead(502, {
      "content-type": "text/plain; charset=utf-8",
      "content-length": String(message.length),
      connection: "close",
    });
    res.end(message);
  });

  req.pipe(upstream);
});

server.listen(port, "127.0.0.1", () => {
  console.log(`proxy listening on http://127.0.0.1:${port}`);
});
