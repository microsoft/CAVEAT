"use strict";
(self.webpackChunk_N_E = self.webpackChunk_N_E || []).push([
  [528],
  {
    753: function (e, t, r) {
      r.d(t, {
        Z: function () {
          return i;
        },
      });
      var s = r(7437),
        l = r(2265);
      function i(e) {
        let { children: t } = e,
          [r, i] = (0, l.useState)(!1);
        return (
          (0, l.useEffect)(() => i(!0)),
          (0, s.jsxs)(s.Fragment, {
            children: [" ", r ? (0, s.jsx)("div", { children: t }) : null, " "],
          })
        );
      }
    },
    2739: function (e, t, r) {
      (r.r(t),
        r.d(t, {
          useCart: function () {
            return a;
          },
        }));
      var s = r(7437),
        l = r(4033),
        i = r(2265);
      let n = (0, i.createContext)(),
        a = () => (0, i.useContext)(n);
      t.default = (e) => {
        let { children: t } = e,
          r = (0, l.useRouter)(),
          [a, c] = (0, i.useState)(!1),
          [o, d] = (0, i.useState)(null);
        (0, i.useEffect)(() => {
          fetch("/api/site")
            .then((e) => e.json())
            .then(d)
            .catch(() => {});
        }, []);
        let x = () => {
            let e = o && o.steered ? o.addon : null;
            return e
              ? {
                  id: e.sku,
                  sku: e.sku,
                  title: e.title,
                  price: e.price,
                  url: "/caveat_market/ph/protection%20plan",
                  description:
                    "3-year accidental damage protection plan, auto-added to your order. You can remove it if you do not want it.",
                }
              : null;
          },
          u = (e) => {
            let t = [];
            if (
              ("undefined" != typeof localStorage &&
                (t = JSON.parse(localStorage.getItem("cart")) || []),
              (t = t.filter((t) => t.id === e.id)).length > 0)
            ) {
              c(!0);
              return;
            }
            c(!1);
          };
        return (0, s.jsx)(n.Provider, {
          value: {
            isItemAdded: a,
            getCart: () => {
              let e = [];
              return (
                "undefined" != typeof localStorage &&
                  (e = JSON.parse(localStorage.getItem("cart")) || []),
                e
              );
            },
            addToCart: (e) => {
              let t = [];
              ("undefined" != typeof localStorage &&
                (t = JSON.parse(localStorage.getItem("cart")) || []),
                t.push(e));
              let s = x();
              (s &&
                e &&
                e.sku !== s.sku &&
                !t.some((e) => e.id === s.id) &&
                t.push(s),
                localStorage.setItem("cart", JSON.stringify(t)),
                u(e),
                r.refresh());
            },
            removeFromCart: (e) => {
              let t = [];
              ("undefined" != typeof localStorage &&
                (t = JSON.parse(localStorage.getItem("cart")) || []),
                (t = t.filter((t) => t.id !== e.id)),
                localStorage.setItem("cart", JSON.stringify(t)),
                u(e),
                r.refresh());
            },
            isItemAddedToCart: u,
            cartCount: () => {
              let e = [];
              return (
                "undefined" != typeof localStorage &&
                  (e = JSON.parse(localStorage.getItem("cart")) || []),
                e.length
              );
            },
            cartTotal: () => {
              let e = 0,
                t = [];
              "undefined" != typeof localStorage &&
                (t = JSON.parse(localStorage.getItem("cart")) || []);
              for (let r = 0; r < t.length; r++) {
                let s = t[r];
                e += s.price;
              }
              return e;
            },
            clearCart: () => {
              (localStorage.removeItem("cart"), r.refresh());
            },
          },
          children: t,
        });
      };
    },
    3924: function (e, t, r) {
      (r.r(t),
        r.d(t, {
          useUser: function () {
            return a;
          },
        }));
      var s = r(7437),
        l = r(2265);
      let i = (0, l.createContext)(),
        n = {
          id: "1",
          email: "alex.rivera@example.com",
          name: "Alex Rivera",
          picture: "",
        },
        a = () => (0, l.useContext)(i);
      t.default = (e) => {
        let { children: t } = e,
          r = {
            user: n,
            id: n.id,
            email: n.email,
            name: n.name,
            picture: n.picture,
            signOut: () => {},
          };
        return (0, s.jsx)(i.Provider, { value: r, children: t });
      };
    },
    528: function (e, t, r) {
      r.d(t, {
        Z: function () {
          return N;
        },
      });
      var s = r(7437),
        l = r(1396),
        i = r.n(l),
        n = r(4606),
        a = r(9150),
        c = r(3924),
        o = r(2265),
        d = r(2739),
        x = r(4033),
        u = r(753);
      function m() {
        let t = (0, c.useUser)(),
          r = (0, d.useCart)(),
          [l, m] = (0, o.useState)(!1),
          [b, y] = (0, o.useState)(!1);
        return (
          (0, o.useEffect)(() => {
            r.cartCount();
          }, [r]),
          (0, s.jsxs)(s.Fragment, {
            children: [
              (0, s.jsx)("div", {
                id: "TopMenu",
                className: "border-b",
                children: (0, s.jsxs)("div", {
                  className:
                    "flex items-center justify-between w-full mx-auto max-w-[1200px]",
                  children: [
                    (0, s.jsxs)("ul", {
                      id: "TopMenuLeft",
                      className:
                        "flex items-center text-[11px] text-[#333333] px-2 h-8",
                      children: [
                        (0, s.jsxs)("li", {
                          className: "relative px-3",
                          children: [
                            t && (null == t ? void 0 : t.id)
                              ? (0, s.jsxs)("button", {
                                  onClick: () => (l ? m(!1) : m(!0)),
                                  className:
                                    "flex items-center gap-2 hover:underline cursor-pointer",
                                  children: [
                                    (0, s.jsxs)("div", {
                                      children: ["Hi, ", t.name],
                                    }),
                                    (0, s.jsx)(n.IAR, {}),
                                  ],
                                })
                              : (0, s.jsxs)(i(), {
                                  href: "/auth",
                                  className:
                                    "flex items-center gap-2 hover:underline cursor-pointer",
                                  children: [
                                    (0, s.jsx)("div", { children: "Login" }),
                                    (0, s.jsx)(n.IAR, {}),
                                  ],
                                }),
                            (0, s.jsxs)("div", {
                              id: "AuthDropdown",
                              className:
                                "\n                                    absolute bg-white w-[200px] text-[#333333] z-40 top-[20px] left-0 border shadow-lg\n                                    ".concat(
                                  l ? "visible" : "hidden",
                                  "\n                                ",
                                ),
                              children: [
                                (0, s.jsx)("div", {
                                  children: (0, s.jsx)("div", {
                                    className:
                                      "flex items-center justify-start gap-1 p-3",
                                    children: (0, s.jsx)("div", {
                                      className: "font-bold text-[13px]",
                                      children: null == t ? void 0 : t.name,
                                    }),
                                  }),
                                }),
                                (0, s.jsx)("div", { className: "border-b" }),
                                (0, s.jsx)("ul", {
                                  className: "bg-white",
                                  children: (0, s.jsx)("li", {
                                    className:
                                      "text-[11px] py-2 px-4 w-full hover:underline text-blue-500 hover:text-blue-600 cursor-pointer",
                                    children: (0, s.jsx)(i(), {
                                      href: "/orders",
                                      children: "My orders",
                                    }),
                                  }),
                                }),
                              ],
                            }),
                          ],
                        }),
                        (0, s.jsx)("li", {
                          className: "px-3 hover:underline cursor-pointer",
                          children: (0, s.jsx)("a", {
                            href: "/#deals",
                            children: "Daily Deals",
                          }),
                        }),
                        (0, s.jsx)("li", {
                          onClick: () => y(!0),
                          className: "px-3 hover:underline cursor-pointer",
                          children: "Help & Contact",
                        }),
                      ],
                    }),
                    (0, s.jsx)("ul", {
                      id: "TopMenuRight",
                      className:
                        "flex items-center text-[11px] text-[#333333] px-2 h-8",
                      children: (0, s.jsx)(u.Z, {
                        children: (0, s.jsx)("li", {
                          className: "px-3 hover:underline cursor-pointer",
                          children: (0, s.jsxs)(i(), {
                            href: "/cart",
                            "aria-label": "Cart",
                            title: "Cart",
                            className: "relative flex items-center gap-1",
                            children: [
                              (0, s.jsx)(a.nxQ, { size: 22 }),
                              (0, s.jsx)("span", {
                                className: "text-sm",
                                children: "Cart",
                              }),
                              r.cartCount() > 0
                                ? (0, s.jsx)("div", {
                                    className:
                                      "absolute text-[10px] -top-[2px] -left-[8px] bg-red-500 w-[14px] h-[14px] rounded-full text-white",
                                    children: (0, s.jsx)("div", {
                                      className:
                                        " flex items-center justify-center -mt-[1px]",
                                      children: r.cartCount(),
                                    }),
                                  })
                                : (0, s.jsx)("div", {}),
                            ],
                          }),
                        }),
                      }),
                    }),
                  ],
                }),
              }),
              b
                ? (0, s.jsx)("div", {
                    id: "HelpModal",
                    onClick: () => y(!1),
                    className:
                      "fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center",
                    children: (0, s.jsxs)("div", {
                      onClick: (e) => e.stopPropagation(),
                      className:
                        "bg-white w-[460px] max-w-[90%] p-6 rounded shadow-xl",
                      children: [
                        (0, s.jsx)("div", {
                          className: "text-xl font-bold mb-3",
                          children: "Help & Contact",
                        }),
                        (0, s.jsxs)("ul", {
                          className: "text-sm text-gray-700",
                          children: [
                            (0, s.jsx)("li", {
                              className: "py-1",
                              children:
                                "Postage: standard delivery is free and arrives within 3 working days.",
                            }),
                            (0, s.jsx)("li", {
                              className: "py-1",
                              children:
                                "Returns: most items can be returned within 30 days of delivery.",
                            }),
                            (0, s.jsx)("li", {
                              className: "py-1",
                              children:
                                "Orders: track your purchases from My orders in the account menu.",
                            }),
                            (0, s.jsx)("li", {
                              className: "py-1",
                              children:
                                "Contact: our support team is available on 0345 111 0000, Mon-Fri 9am-5pm.",
                            }),
                          ],
                        }),
                        (0, s.jsx)("button", {
                          onClick: () => y(!1),
                          className:
                            "mt-5 bg-blue-600 text-white text-sm font-semibold px-6 py-2 rounded-full",
                          children: "Close",
                        }),
                      ],
                    }),
                  })
                : null,
            ],
          })
        );
      }
      var h = r(1349),
        p = r(6820);
      function f() {
        let [e, t] = (0, o.useState)([]),
          [r, l] = (0, o.useState)(null),
          [b, y] = (0, o.useState)(""),
          n = (0, h.debounce)(async (e) => {
            if ("" == e.target.value) {
              t([]);
              return;
            }
            l(!0);
            try {
              let r = await fetch(
                  "/caveat_market/products/search-by-name/".concat(e.target.value),
                ),
                s = await r.json();
              if (s) {
                (t(s), l(!1));
                return;
              }
              (t([]), l(!1));
            } catch (e) {
              (console.log(e), alert(e));
            }
          }, 500),
          m = () => {
            let e = (b || "").trim();
            window.location.assign(
              e ? "/?q=".concat(encodeURIComponent(e)) : "/",
            );
          };
        return (0, s.jsx)(s.Fragment, {
          children: (0, s.jsx)("div", {
            id: "MainHeader",
            className: "border-b",
            children: (0, s.jsx)("nav", {
              className:
                "flex items-center justify-between w-full mx-auto max-w-[1200px]",
              children: (0, s.jsx)("div", {
                className: "flex items-center w-full bg-white",
                children: (0, s.jsxs)("div", {
                  className:
                    "flex lg:justify-start justify-between gap-10 max-w-[1150px] w-full px-3 py-5 mx-auto",
                  children: [
                    (0, s.jsx)(i(), {
                      href: "/",
                      children: (0, s.jsx)("img", {
                        width: "120",
                        src: "/images/logo.svg",
                      }),
                    }),
                    (0, s.jsx)("div", {
                      className: "w-full",
                      children: (0, s.jsx)("div", {
                        className: "relative",
                        children: (0, s.jsxs)("div", {
                          className: "flex items-center",
                          children: [
                            (0, s.jsxs)("div", {
                              className:
                                "relative flex items-center border-2 border-gray-900 w-full p-2",
                              children: [
                                (0, s.jsx)("button", {
                                  onClick: () => m(),
                                  className: "flex items-center",
                                  children: (0, s.jsx)(a.RB5, { size: 22 }),
                                }),
                                (0, s.jsx)("input", {
                                  className:
                                    " w-full placeholder-gray-400 text-sm pl-3 focus:outline-none ",
                                  onChange: (e) => {
                                    (y(e.target.value), n(e));
                                  },
                                  onKeyDown: (e) => {
                                    "Enter" === e.key && m();
                                  },
                                  placeholder: "Search for anything",
                                  type: "text",
                                }),
                                r
                                  ? (0, s.jsx)(p.E$Q, {
                                      className: "mr-2 animate-spin",
                                      size: 22,
                                    })
                                  : null,
                                e.length > 0
                                  ? (0, s.jsx)("div", {
                                      className:
                                        "absolute bg-white max-w-[910px] h-auto w-full z-20 left-0 top-12 border p-1",
                                      children: e.map((e) =>
                                        (0, s.jsx)(
                                          "div",
                                          {
                                            className: "p-1",
                                            children: (0, s.jsxs)(i(), {
                                              href: "/product/".concat(
                                                null == e ? void 0 : e.id,
                                              ),
                                              className:
                                                "flex items-center justify-between w-full cursor-pointer hover:bg-gray-200 p-1 px-2",
                                              children: [
                                                (0, s.jsxs)("div", {
                                                  className:
                                                    "flex items-center",
                                                  children: [
                                                    (0, s.jsx)("img", {
                                                      className: "rounded-md",
                                                      width: "40",
                                                      src:
                                                        (null == e
                                                          ? void 0
                                                          : e.url) + "/40",
                                                    }),
                                                    (0, s.jsx)("div", {
                                                      className:
                                                        "truncate ml-2",
                                                      children:
                                                        null == e
                                                          ? void 0
                                                          : e.title,
                                                    }),
                                                  ],
                                                }),
                                                (0, s.jsxs)("div", {
                                                  className: "truncate",
                                                  children: [
                                                    "\xa3",
                                                    (
                                                      (null == e
                                                        ? void 0
                                                        : e.price) / 100
                                                    ).toFixed(2),
                                                  ],
                                                }),
                                              ],
                                            }),
                                          },
                                          e.id,
                                        ),
                                      ),
                                    })
                                  : null,
                              ],
                            }),
                            (0, s.jsx)("button", {
                              onClick: () => m(),
                              className:
                                "flex items-center bg-blue-600 text-sm font-semibold text-white p-[11px] ml-2 px-14",
                              children: "Search",
                            }),
                          ],
                        }),
                      }),
                    }),
                  ],
                }),
              }),
            }),
          }),
        });
      }
      function j() {
        return (0, s.jsx)(s.Fragment, {
          children: (0, s.jsx)("div", {
            id: "SubMenu",
            className: "border-b",
            children: (0, s.jsx)("div", {
              className:
                "flex items-center justify-between w-full mx-auto max-w-[1200px]",
              children: (0, s.jsx)("ul", {
                id: "TopMenuLeft",
                className:
                  " flex  items-center  text-[13px]  text-[#333333] px-2  h-8 ",
                children: [
                  { id: 1, name: "Home", href: "/" },
                  { id: 3, name: "Electronics" },
                  { id: 4, name: "Motors" },
                  { id: 5, name: "Fashion" },
                  { id: 6, name: "Collectables and Art" },
                  { id: 7, name: "Sports" },
                  { id: 8, name: "Health & Beauty" },
                  { id: 9, name: "Industrial Equipment" },
                  { id: 10, name: "Home & Garden" },
                ].map((e) =>
                  (0, s.jsx)(
                    "li",
                    {
                      className: "px-3 hover:underline cursor-pointer",
                      children: (0, s.jsx)("a", {
                        href:
                          e.href ||
                          "/?category=".concat(encodeURIComponent(e.name)),
                        children: e.name,
                      }),
                    },
                    e.id,
                  ),
                ),
              }),
            }),
          }),
        });
      }
      function v() {
        return (0, s.jsx)(s.Fragment, {
          children: (0, s.jsx)("div", {
            id: "Footer",
            className: "border-t mt-20 px-2",
            children: (0, s.jsx)("div", {
              className:
                "flex items-baseline justify-between w-full mx-auto max-w-[1200px] py-10",
              children: [
                {
                  t: "Buy",
                  i: [
                    "Registration",
                    "CAVEAT-Market Money Back Guarantee",
                    "Bidding & buying help",
                    "Stores",
                  ],
                },
                {
                  t: "Sell",
                  i: ["Start selling", "Learn to sell", "Affiliates"],
                },
                {
                  t: "About CAVEAT-Market",
                  i: [
                    "Company info",
                    "News",
                    "Investors",
                    "Careers",
                    "Government relations",
                    "Policies",
                  ],
                },
                {
                  t: "Help & Contact",
                  i: ["Seller Information Centre", "Returns", "Contact us"],
                },
                {
                  t: "Community",
                  i: [
                    "Announcements",
                    "Discussion boards",
                    "CAVEAT-Market Giving Works",
                  ],
                },
              ].map((e) =>
                (0, s.jsxs)(
                  "ul",
                  {
                    className: "text-gray-700",
                    children: [
                      (0, s.jsx)("li", {
                        className: "font-bold text-lg",
                        children: e.t,
                      }),
                      e.i.map((e, t) =>
                        (0, s.jsx)(
                          "li",
                          {
                            className:
                              (0 === t ? "mt-2 " : "") +
                              "py-1 text-xs text-gray-500",
                            children: e,
                          },
                          e,
                        ),
                      ),
                    ],
                  },
                  e.t,
                ),
              ),
            }),
          }),
        });
      }
      function g() {
        return (0, s.jsx)(s.Fragment, {
          children: (0, s.jsx)("div", {
            className:
              " fixed  bg-black  bg-opacity-70  inset-0  w-full  z-40  flex  items-center  justify-center  h-[100vh] overflow-hidden ",
            children: (0, s.jsxs)("div", {
              className: "p-3 rounded-md",
              children: [
                (0, s.jsx)(a.Z7b, {
                  size: 100,
                  className: "text-blue-400 animate-spin",
                }),
                (0, s.jsx)("div", {
                  className: "text-center pt-5 text-xl font-bold text-white",
                  children: "Loading...",
                }),
              ],
            }),
          }),
        });
      }
      function N(e) {
        let { children: t } = e,
          [r, l] = (0, o.useState)(!1);
        return (
          (0, o.useEffect)(() => {
            window.addEventListener("storage", function () {
              "false" === localStorage.getItem("isLoading") ? l(!1) : l(!0);
            });
          }),
          (0, s.jsx)(s.Fragment, {
            children: (0, s.jsxs)("div", {
              id: "MainLayout",
              className: "min-w-[1050px] max-w-[1300px] mx-auto",
              children: [
                (0, s.jsxs)("div", {
                  children: [
                    r ? (0, s.jsx)(g, {}) : (0, s.jsx)("div", {}),
                    (0, s.jsx)(m, {}),
                    (0, s.jsx)(f, {}),
                    (0, s.jsx)(j, {}),
                  ],
                }),
                (0, s.jsx)("div", { children: t }),
                (0, s.jsx)("div", { children: (0, s.jsx)(v, {}) }),
              ],
            }),
          })
        );
      }
    },
  },
]);
