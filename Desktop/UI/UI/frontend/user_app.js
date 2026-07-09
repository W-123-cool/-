/**
 * 占位：可改为调用与 PyQt 相同的 REST 接口。
 * 浏览器打开 user_app.html 时需解决 CORS；本地开发可用 file:// 或静态服务器。
 */
const base = "http://127.0.0.1:8000";
document.getElementById("base").textContent = base;

fetch(`${base}/api/health`)
  .then((r) => r.json())
  .then((j) => {
    document.getElementById("out").textContent = JSON.stringify(j, null, 2);
  })
  .catch((e) => {
    document.getElementById("out").textContent = String(e);
  });
