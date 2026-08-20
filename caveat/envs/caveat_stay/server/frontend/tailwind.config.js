/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        caveat_stay: {
          primary: '#00A2ED',
          'primary-dark': '#0078B3',
          teal: '#00A2ED',
          orange: '#00A2ED',
          dark: '#484848',
          gray: '#767676',
          'light-gray': '#EBEBEB',
          'bg-gray': '#F7F7F7',
        }
      },
      fontFamily: {
        caveat_stay: ['Circular', '-apple-system', 'BlinkMacSystemFont', 'Roboto', 'Helvetica Neue', 'sans-serif'],
      }
    },
  },
  plugins: [],
}
