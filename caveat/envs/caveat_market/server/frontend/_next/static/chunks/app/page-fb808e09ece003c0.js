(self.webpackChunk_N_E = self.webpackChunk_N_E || []).push([
  [931],
  {
    9796: function (e, n, s) {
      Promise.resolve().then(s.bind(s, 4897));
    },
    56: function (e, n, s) {
      "use strict";
      s.d(n, {
        Z: function () {
          return l;
        },
      });
      var i = s(7437),
        r = s(1396),
        t = s.n(r);
      function l(e) {
        let { product: n } = e;
        return (0, i.jsx)(i.Fragment, {
          children: (0, i.jsxs)(t(), {
            href: "/product/".concat(null == n ? void 0 : n.id),
            className:
              "max-w-[200px] p-1.5 border border-gray-50 hover:border-gray-200 hover:shadow-xl bg-gray-100 rounded mx-auto",
            children: [
              (null == n ? void 0 : n.url)
                ? (0, i.jsx)("img", {
                    className: "rounded cursor-pointer",
                    src: n.url + "/190",
                  })
                : null,
              (0, i.jsxs)("div", {
                className: "pt-2 px-1",
                children: [
                  (0, i.jsx)("div", {
                    className:
                      "font-semibold text-[15px] hover:underline cursor-pointer",
                    children: null == n ? void 0 : n.title,
                  }),
                  (0, i.jsxs)("div", {
                    className: "font-extrabold",
                    children: [
                      "\xa3",
                      ((null == n ? void 0 : n.price) / 100).toFixed(2),
                    ],
                  }),
                  (0, i.jsxs)("div", {
                    className:
                      "relative flex items-center text-[12px] text-gray-500",
                    children: [
                      (0, i.jsxs)("div", {
                        className: "line-through",
                        children: [
                          "\xa3",
                          (
                            ((null == n ? void 0 : n.price) * 1.2) /
                            100
                          ).toFixed(2),
                        ],
                      }),
                      (0, i.jsx)("div", { className: "px-2", children: "-" }),
                      (0, i.jsx)("div", {
                        className: "line-through",
                        children: "20%",
                      }),
                    ],
                  }),
                  (0, i.jsx)("div", {
                    className: "text-[12px] font-semibold ".concat(
                      (null == n ? void 0 : n.condition) === "New"
                        ? "text-[#0b7d3e]"
                        : "text-[#00A2ED]",
                    ),
                    children: (null == n ? void 0 : n.condition)
                      ? "Condition: ".concat(n.condition)
                      : null,
                  }),
                  (0, i.jsx)("div", {
                    className: "text-[12px] text-gray-500",
                    children: "Free postage",
                  }),
                  (null == n ? void 0 : n.rating)
                    ? (0, i.jsxs)("div", {
                        className: "text-[12px] text-[#555] mt-0.5",
                        children: [
                          "★ ",
                          n.rating,
                          (null == n ? void 0 : n.reviews)
                            ? (0, i.jsxs)("span", {
                                className: "text-gray-400",
                                children: [" (", n.reviews, " ratings)"],
                              })
                            : null,
                        ],
                      })
                    : null,
                  (null == n ? void 0 : n.description)
                    ? (0, i.jsx)("div", {
                        className:
                          "text-[11px] text-gray-600 mt-1 leading-snug",
                        children: n.description,
                      })
                    : null,
                  (null == n ? void 0 : n.sponsored)
                    ? (0, i.jsx)("div", {
                        className: "text-[11px] text-gray-400",
                        children: "Sponsored",
                      })
                    : null,
                ],
              }),
            ],
          }),
        });
      }
    },
    1913: function (e, n) {
      "use strict";
      n.Z = (e) => {
        (localStorage.setItem("isLoading", e),
          window.dispatchEvent(new Event("storage")));
      };
    },
    4897: function (e, n, s) {
      "use strict";
      (s.r(n),
        s.d(n, {
          default: function () {
            return o;
          },
        }));
      var i = s(7437),
        r = s(2265),
        t = s(5278);
      function l() {
        return (0, i.jsx)(i.Fragment, {
          children: (0, i.jsx)("div", {
            className: "max-w-[1200px] mx-auto",
            children: (0, i.jsxs)(t.lr, {
              showArrows: !0,
              autoPlay: !0,
              interval: 3e3,
              infiniteLoop: !0,
              showThumbs: !1,
              onClickItem: () => window.location.assign("/#deals"),
              children: [
                (0, i.jsx)("div", {
                  children: (0, i.jsx)("img", { src: "/images/banner/1.png" }),
                }),
                (0, i.jsx)("div", {
                  children: (0, i.jsx)("img", { src: "/images/banner/2.png" }),
                }),
                (0, i.jsx)("div", {
                  children: (0, i.jsx)("img", { src: "/images/banner/3.png" }),
                }),
              ],
            }),
          }),
        });
      }
      s(2169);
      var a = s(56),
        d = s(528),
        c = s(1913),
        u = s(4033);
      function v(e) {
        var n = Math.ceil(e.total / 24),
          l = [],
          t = function (t, o, x, y, j) {
            l.push(
              (0, i.jsx)(
                "button",
                {
                  type: "button",
                  disabled: !!x,
                  "aria-label": y,
                  "aria-current": o ? "page" : void 0,
                  onClick: function () {
                    x || o || (e.go(t), window.scrollTo(0, 0));
                  },
                  style: {
                    minWidth: "36px",
                    height: "36px",
                    padding: "0 12px",
                    margin: "0 4px",
                    borderRadius: "18px",
                    border: o ? "1px solid #191919" : "1px solid #c7c7c7",
                    background: o ? "#191919" : "#ffffff",
                    color: x ? "#c7c7c7" : o ? "#ffffff" : "#191919",
                    fontSize: "14px",
                    fontWeight: o ? 700 : 400,
                    cursor: x || o ? "default" : "pointer",
                  },
                  children: j,
                },
                y,
              ),
            );
          };
        t(e.page - 1, !1, e.page <= 1, "Previous page", "Previous");
        for (var o = 1; o <= n; o++) t(o, o === e.page, !1, "Page " + o, o);
        t(e.page + 1, !1, e.page >= n, "Next page", "Next");
        return (0, i.jsx)("nav", {
          "aria-label": "Pagination",
          role: "navigation",
          style: {
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            margin: "28px 0 44px",
          },
          children: l,
        });
      }
      function o() {
        let [e, n] = (0, r.useState)([]),
          [f, h] = (0, r.useState)(null),
          [b, m] = (0, r.useState)(1),
          w = (0, u.useSearchParams)(),
          k = w ? w.toString() : "",
          z = async () => {
            (0, c.Z)(!0);
            let t = new URLSearchParams(k),
              i = (t.get("q") || "").trim(),
              r = (t.get("category") || "").trim(),
              x = i
                ? "/caveat_market/products/search-by-name/".concat(encodeURIComponent(i))
                : r
                  ? "/caveat_market/products?category=".concat(encodeURIComponent(r))
                  : "/caveat_market/products",
              e = await fetch(x),
              s = await e.json();
            (n([]), n(s || []), h({ q: i, cat: r }), m(1), (0, c.Z)(!1));
          };
        (0, r.useEffect)(() => {
          z();
        }, [k]);
        let g = f && (f.q || f.cat),
          p = g
            ? f.q
              ? 'Results for "'.concat(f.q, '"')
              : f.cat
            : "Products";
        return (0, i.jsx)(i.Fragment, {
          children: (0, i.jsxs)(d.Z, {
            children: [
              g ? null : (0, i.jsx)(l, {}),
              (0, i.jsxs)("div", {
                id: "deals",
                className: "max-w-[1200px] mx-auto",
                children: [
                  (0, i.jsx)("div", {
                    className: "text-2xl font-bold mt-4 mb-6 px-4",
                    children: p,
                  }),
                  g
                    ? (0, i.jsx)("div", {
                        className: "text-sm text-gray-600 -mt-4 mb-4 px-4",
                        children: ""
                          .concat(e.length, " ")
                          .concat(1 === e.length ? "result" : "results"),
                      })
                    : null,
                  g && 0 === e.length
                    ? (0, i.jsxs)("div", {
                        className: "px-4 py-10",
                        children: [
                          (0, i.jsx)("div", {
                            className: "text-lg font-semibold",
                            children: "0 results ".concat(
                              f.q
                                ? 'for "'.concat(f.q, '"')
                                : "in ".concat(f.cat),
                            ),
                          }),
                          (0, i.jsx)("div", {
                            className: "text-sm text-gray-600 mt-2",
                            children:
                              "Try checking your spelling, using more general keywords, or browsing a different category.",
                          }),
                          (0, i.jsx)("a", {
                            href: "/",
                            className:
                              "inline-block mt-4 text-blue-600 underline",
                            children: "Browse all items",
                          }),
                        ],
                      })
                    : (0, i.jsxs)(i.Fragment, {
                        children: [
                          (0, i.jsx)("div", {
                            className: "grid grid-cols-5 gap-4",
                            children: (g
                              ? e
                              : e.slice(24 * (b - 1), 24 * b)
                            ).map((e) => (0, i.jsx)(a.Z, { product: e }, e.id)),
                          }),
                          !g && e.length > 24
                            ? (0, i.jsx)(v, { total: e.length, page: b, go: m })
                            : null,
                        ],
                      }),
                ],
              }),
            ],
          }),
        });
      }
    },
  },
  function (e) {
    (e.O(0, [447, 115, 712, 17, 339, 528, 971, 596, 744], function () {
      return e((e.s = 9796));
    }),
      (_N_E = e.O()));
  },
]);
