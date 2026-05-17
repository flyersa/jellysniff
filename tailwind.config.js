module.exports = {
  content: [
    './app/templates/**/*.html',
    './app/static/js/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        accent: {
          DEFAULT: '#AA5CC3',
          bright: '#C77CDD',
          dim:    '#6B3A7A',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '0px',
        sm: '0px',
        md: '2px',
        lg: '2px',
      },
    },
  },
  plugins: [],
};
