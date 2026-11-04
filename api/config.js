import fixtures from "../web/fixtures.json" with { type: "json" };
export default function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  res.status(200).json({available:false,deployment:"preview",access_required:false,examples_available:true,fixtures,message:"Live Vultr repairs are awaiting the operator connection."});
}
