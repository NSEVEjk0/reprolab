export default function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  res.status(503).json({error:"Live repairs require the dedicated Vultr controller and Serverless Inference connection."});
}
