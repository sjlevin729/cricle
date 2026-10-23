// Where the game's server is. Filled in during setup (SETUP.md, step 5).
// Both values are meant to be public: the publishable key only lets the page call the game's functions.
// Never put the secret key here.
window.CRICLE_CONFIG = {
  // The address shown in shared results
  siteUrl: "playcricle.com",
  supabaseUrl: "https://bklxlytlyaxufumludvo.supabase.co",
  supabaseKey: "sb_publishable_WwQwPyvzGjlCadH8R7g6rg_kovWJ5yk",
  // Google Analytics 4 Measurement ID (starts G-). Leave empty for no analytics and no cookie bar.
  gaMeasurementId: "G-CB63B00DCW",
  // Meta Pixel ID (a long number from Events Manager). Leave empty for no Pixel. Loads only after the visitor allows cookies.
  metaPixelId: "",
  // The sponsor, shown under the scoreboard, on the result screen and in the footer.
  // Leave name empty to advertise the space instead.
  //   logo:   a square image, a full https:// address or a file put in docs/ (e.g. "sponsor-logo.png")
  //   colour: their brand colour for the button and border, e.g. "#c8102e"
  //   cta:    button text, e.g. "Shop the range" (defaults to "Visit <name>")
  sponsor: { name: "", line: "", url: "", logo: "", colour: "", cta: "" },
};
