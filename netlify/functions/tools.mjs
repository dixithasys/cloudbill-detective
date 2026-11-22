/**
 * Netlify Serverless Function (netlify/functions/tools.mjs)
 * Implements the strictly read-only AWS SigV4 client and all /tools/* endpoints
 * so live AWS credentials work directly on Netlify without needing an external server.
 */

import crypto from "node:crypto";

const DEMO_DATA = {
  waste: {
    region: "us-east-1",
    mode: "fixture",
    waste_count: 7,
    total_estimated_monthly_cost: 286.55,
    resources: [
      { resource_id: "i-0f9e8d7c6b5a00001", resource_type: "ec2_instance", region: "us-east-1", reason: "Underutilized EC2 instance (m5.xlarge, 7-day avg CPU 1.4% < 5.0% threshold)", estimated_monthly_cost: 140.16, created_by: "charlie.ml", creating_event: "RunInstances", created_at: "2026-10-18T19:40:12+00:00", source_ip: "192.0.2.77" },
      { resource_id: "i-0f9e8d7c6b5a00002", resource_type: "ec2_instance", region: "us-east-1", reason: "Underutilized EC2 instance (t3.large, 7-day avg CPU 2.3% < 5.0% threshold)", estimated_monthly_cost: 60.74, created_by: "alice.dev", creating_event: "RunInstances", created_at: "2026-10-25T14:10:05+00:00", source_ip: "198.51.100.14" },
      { resource_id: "vol-0a1b2c3d4e5f00001", resource_type: "ebs_volume", region: "us-east-1", reason: "Unattached EBS volume (state=available, 500 GiB gp3)", estimated_monthly_cost: 40.00, created_by: "alice.dev", creating_event: "CreateVolume", created_at: "2026-10-14T16:22:10+00:00", source_ip: "198.51.100.14" },
      { resource_id: "snap-0fedcba9876500001", resource_type: "ebs_snapshot", region: "us-east-1", reason: "Stale EBS snapshot older than 90 days (255 days old, 400 GiB)", estimated_monthly_cost: 20.00, created_by: "backup-lambda-role", creating_event: "CreateSnapshot", created_at: "2026-03-10T04:00:00+00:00", source_ip: "lambda.amazonaws.com" },
      { resource_id: "snap-0fedcba9876500002", resource_type: "ebs_snapshot", region: "us-east-1", reason: "Stale EBS snapshot older than 90 days (173 days old, 240 GiB)", estimated_monthly_cost: 12.00, created_by: "bob.ops", creating_event: "CreateSnapshot", created_at: "2026-06-01T02:30:00+00:00", source_ip: "203.0.113.50" },
      { resource_id: "vol-0a1b2c3d4e5f00002", resource_type: "ebs_volume", region: "us-east-1", reason: "Unattached EBS volume (state=available, 100 GiB gp2)", estimated_monthly_cost: 10.00, created_by: "ci-deploy-bot", creating_event: "CreateVolume", created_at: "2026-09-02T09:15:00+00:00", source_ip: "198.51.100.88" },
      { resource_id: "eipalloc-0123456789abcdef1", resource_type: "elastic_ip", region: "us-east-1", reason: "Unassociated Elastic IP (54.210.111.42 not attached to any instance or ENI)", estimated_monthly_cost: 3.65, created_by: "bob.ops", creating_event: "AllocateAddress", created_at: "2026-10-20T11:05:44+00:00", source_ip: "203.0.113.50" }
    ]
  },
  costs: {
    SERVICE: [
      { group: "Amazon Elastic Compute Cloud - Compute", cost_usd: 412.40 },
      { group: "Amazon Elastic Block Store", cost_usd: 148.50 },
      { group: "Amazon Virtual Private Cloud", cost_usd: 29.20 },
      { group: "Amazon Simple Storage Service", cost_usd: 18.75 },
      { group: "AWS CloudTrail", cost_usd: 4.10 }
    ],
    REGION: [
      { group: "us-east-1", cost_usd: 565.80 },
      { group: "us-west-2", cost_usd: 47.15 }
    ],
    USAGE_TYPE: [
      { group: "BoxUsage:m5.xlarge", cost_usd: 140.16 },
      { group: "EBS:VolumeUsage.gp3", cost_usd: 86.50 },
      { group: "BoxUsage:t3.large", cost_usd: 60.74 },
      { group: "EBS:SnapshotUsage", cost_usd: 52.00 },
      { group: "USE1-PublicIPv4:IdleAddress", cost_usd: 3.65 }
    ]
  }
};

const EBS_PRICING = { gp3: 0.08, gp2: 0.10, io1: 0.125, io2: 0.125, st1: 0.045, sc1: 0.015, standard: 0.05 };

function sha256Hex(data) {
  return crypto.createHash("sha256").update(data, "utf8").digest("hex");
}

function hmac(key, data, encoding) {
  return crypto.createHmac("sha256", key).update(data, "utf8").digest(encoding);
}

function getSigningKey(secretKey, dateStamp, regionName, serviceName) {
  const kDate = hmac("AWS4" + secretKey, dateStamp);
  const kRegion = hmac(kDate, regionName);
  const kService = hmac(kRegion, serviceName);
  return hmac(kService, "aws4_request");
}

async function awsSignedPost({ service, region, host, contentType, amzTarget, body, creds }) {
  const now = new Date();
  const amzDate = now.toISOString().replace(/[:-]|\.\d{3}/g, "");
  const dateStamp = amzDate.slice(0, 8);
  const payloadHash = sha256Hex(body);

  const headersObj = {
    "content-type": contentType,
    "host": host,
    "x-amz-date": amzDate,
  };
  if (amzTarget) headersObj["x-amz-target"] = amzTarget;
  if (creds.sessionToken) headersObj["x-amz-security-token"] = creds.sessionToken;

  const sortedKeys = Object.keys(headersObj).sort();
  const canonicalHeaders = sortedKeys.map(k => `${k}:${headersObj[k]}\n`).join("");
  const signedHeaders = sortedKeys.join(";");

  const canonicalRequest = [
    "POST",
    "/",
    "",
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");

  const credentialScope = `${dateStamp}/${region}/${service}/aws4_request`;
  const stringToSign = [
    "AWS4-HMAC-SHA256",
    amzDate,
    credentialScope,
    sha256Hex(canonicalRequest),
  ].join("\n");

  const signingKey = getSigningKey(creds.secretAccessKey, dateStamp, region, service);
  const signature = hmac(signingKey, stringToSign, "hex");
  const authHeader = `AWS4-HMAC-SHA256 Credential=${creds.accessKeyId}/${credentialScope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;

  const fetchHeaders = {
    "Content-Type": contentType,
    "X-Amz-Date": amzDate,
    "Authorization": authHeader,
  };
  if (amzTarget) fetchHeaders["X-Amz-Target"] = amzTarget;
  if (creds.sessionToken) fetchHeaders["X-Amz-Security-Token"] = creds.sessionToken;

  const resp = await fetch(`https://${host}/`, {
    method: "POST",
    headers: fetchHeaders,
    body,
  });
  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(`AWS ${service} (${resp.status}): ${text.slice(0, 320)}`);
  }
  return text;
}

function getCredsFromReq(req, bodyObj = {}) {
  // ONLY read credentials explicitly sent by the current browser tab (never read Netlify's internal Lambda AWS_* env vars!)
  const accessKeyId = (
    bodyObj.aws_access_key_id ||
    req.headers.get("x-aws-access-key-id") ||
    ""
  ).trim().replace(/^["']|["']$/g, "");
  const secretAccessKey = (
    bodyObj.aws_secret_access_key ||
    req.headers.get("x-aws-secret-access-key") ||
    ""
  ).trim().replace(/^["']|["']$/g, "");
  let sessionToken = (
    bodyObj.aws_session_token ||
    req.headers.get("x-aws-session-token") ||
    ""
  ).trim().replace(/^["']|["']$/g, "");

  // Permanent IAM User keys start with AKIA and must NEVER send X-Amz-Security-Token
  if (accessKeyId.toUpperCase().startsWith("AKIA")) {
    sessionToken = "";
  }

  const mode = (
    bodyObj.mode ||
    req.headers.get("x-aws-mode") ||
    (accessKeyId && secretAccessKey ? "live" : "fixture")
  ).toLowerCase();
  const region = (
    bodyObj.region ||
    req.headers.get("x-aws-region") ||
    "ap-southeast-2"
  ).trim();

  return { accessKeyId, secretAccessKey, sessionToken, mode, region };
}

async function verifyStsIdentity(creds) {
  if (creds.mode === "fixture" || !creds.accessKeyId || !creds.secretAccessKey) {
    return {
      mode: "fixture",
      connected: false,
      region: creds.region,
      account_id: "fixture-demo-account",
      arn: "arn:aws:iam::000000000000:user/fixture-mode",
      llm: { provider: "free" },
    };
  }
  try {
    const xml = await awsSignedPost({
      service: "sts",
      region: "us-east-1",
      host: "sts.amazonaws.com",
      contentType: "application/x-www-form-urlencoded; charset=utf-8",
      body: "Action=GetCallerIdentity&Version=2011-06-15",
      creds,
    });
    const account = (xml.match(/<Account>([^<]+)<\/Account>/) || [])[1] || "unknown";
    const arn = (xml.match(/<Arn>([^<]+)<\/Arn>/) || [])[1] || "unknown";
    return {
      mode: "live",
      connected: true,
      region: creds.region,
      account_id: account,
      arn,
      message: `Connected to live AWS Account ${account} (${arn})`,
      llm: { provider: "free" },
    };
  } catch (err) {
    return {
      mode: "live",
      connected: false,
      region: creds.region,
      account_id: null,
      arn: null,
      message: `AWS authentication failed: ${err.message}`,
      llm: { provider: "free" },
    };
  }
}

async function liveCostSummary(days, groupBy, creds) {
  if (creds.mode === "fixture" || !creds.accessKeyId || !creds.secretAccessKey) {
    const grp = groupBy.toUpperCase();
    return {
      days,
      group_by: grp,
      time_period: { start: "2026-10-22", end: "2026-11-21" },
      source: "fixture",
      cache_hit: false,
      api_cost_saved_usd: 0.01,
      total_cost_usd: 612.95,
      credits_applied_usd: 0,
      net_after_credits_usd: 612.95,
      breakdown: DEMO_DATA.costs[grp] || DEMO_DATA.costs.SERVICE,
    };
  }

  const now = new Date();
  const endDt = new Date(now.getTime() + 86400000);
  const startDt = new Date(now.getTime() - days * 86400000);
  const startDate = startDt.toISOString().slice(0, 10);
  const endDate = endDt.toISOString().slice(0, 10);
  const normalizedGroup = groupBy.toUpperCase();

  try {
    const usageBody = JSON.stringify({
      TimePeriod: { Start: startDate, End: endDate },
      Granularity: days <= 14 ? "DAILY" : "MONTHLY",
      Metrics: ["UnblendedCost"],
      GroupBy: [{ Type: "DIMENSION", Key: normalizedGroup }],
      Filter: { Not: { Dimensions: { Key: "RECORD_TYPE", Values: ["Credit", "Refund"] } } },
    });

    const rawText = await awsSignedPost({
      service: "ce",
      region: "us-east-1",
      host: "ce.us-east-1.amazonaws.com",
      contentType: "application/x-amz-json-1.1",
      amzTarget: "AWSInsightsIndexService.GetCostAndUsage",
      body: usageBody,
      creds,
    });
    const ceData = JSON.parse(rawText);

    const totals = {};
    for (const period of ceData.ResultsByTime || []) {
      for (const g of period.Groups || []) {
        const key = (g.Keys && g.Keys[0]) || "Unknown";
        const amt = parseFloat(g.Metrics?.UnblendedCost?.Amount || "0");
        if (amt > 0) totals[key] = (totals[key] || 0) + amt;
      }
    }

    const breakdown = Object.entries(totals)
      .map(([group, val]) => ({ group, cost_usd: Math.round(val * 100) / 100 }))
      .filter(x => x.cost_usd > 0)
      .sort((a, b) => b.cost_usd - a.cost_usd);

    const totalSpend = Math.round(breakdown.reduce((acc, x) => acc + x.cost_usd, 0) * 100) / 100;

    let creditsApplied = 0;
    try {
      const creditBody = JSON.stringify({
        TimePeriod: { Start: startDate, End: endDate },
        Granularity: "MONTHLY",
        Metrics: ["UnblendedCost"],
        Filter: { Dimensions: { Key: "RECORD_TYPE", Values: ["Credit"] } },
      });
      const creditText = await awsSignedPost({
        service: "ce",
        region: "us-east-1",
        host: "ce.us-east-1.amazonaws.com",
        contentType: "application/x-amz-json-1.1",
        amzTarget: "AWSInsightsIndexService.GetCostAndUsage",
        body: creditBody,
        creds,
      });
      const creditData = JSON.parse(creditText);
      for (const p of creditData.ResultsByTime || []) {
        const amt = parseFloat(p.Total?.UnblendedCost?.Amount || "0");
        creditsApplied += Math.abs(amt);
      }
      creditsApplied = Math.round(creditsApplied * 100) / 100;
    } catch (_) {}

    return {
      days,
      group_by: normalizedGroup,
      time_period: { start: startDate, end: endDate },
      source: "aws_cost_explorer",
      cache_hit: false,
      api_cost_saved_usd: 0,
      total_cost_usd: totalSpend,
      credits_applied_usd: creditsApplied,
      net_after_credits_usd: Math.max(0, Math.round((totalSpend - creditsApplied) * 100) / 100),
      breakdown,
    };
  } catch (err) {
    return {
      days,
      group_by: normalizedGroup,
      time_period: { start: startDate, end: endDate },
      source: "aws_cost_explorer_error",
      total_cost_usd: 0,
      credits_applied_usd: 0,
      net_after_credits_usd: 0,
      breakdown: [],
      error: err.message,
    };
  }
}

async function liveFindWaste(region, creds) {
  if (creds.mode === "fixture" || !creds.accessKeyId || !creds.secretAccessKey) {
    return { ...DEMO_DATA.waste, region };
  }

  const findings = [];
  const warnings = [];
  const host = `ec2.${region}.amazonaws.com`;

  // 1. Unattached EBS volumes
  try {
    const xml = await awsSignedPost({
      service: "ec2",
      region,
      host,
      contentType: "application/x-www-form-urlencoded; charset=utf-8",
      body: "Action=DescribeVolumes&Version=2016-11-15&Filter.1.Name=status&Filter.1.Value.1=available",
      creds,
    });
    const items = xml.split("<item>").slice(1);
    for (const item of items) {
      const volId = (item.match(/<volumeId>(vol-[^<]+)<\/volumeId>/) || [])[1];
      const status = (item.match(/<status>([^<]+)<\/status>/) || [])[1];
      const size = parseInt((item.match(/<size>(\d+)<\/size>/) || [])[1] || "0", 10);
      const vtype = (item.match(/<volumeType>([^<]+)<\/volumeType>/) || [])[1] || "gp3";
      if (volId && status === "available") {
        const rate = EBS_PRICING[vtype.toLowerCase()] || 0.08;
        findings.push({
          resource_id: volId,
          resource_type: "ebs_volume",
          region,
          reason: `Unattached EBS volume (state=available, ${size} GiB ${vtype})`,
          estimated_monthly_cost: Math.round(size * rate * 100) / 100,
        });
      }
    }
  } catch (err) {
    warnings.push(`ec2:DescribeVolumes failed: ${err.message}`);
  }

  // 2. Unassociated Elastic IPs
  try {
    const xml = await awsSignedPost({
      service: "ec2",
      region,
      host,
      contentType: "application/x-www-form-urlencoded; charset=utf-8",
      body: "Action=DescribeAddresses&Version=2016-11-15",
      creds,
    });
    const items = xml.split("<item>").slice(1);
    for (const item of items) {
      const allocId = (item.match(/<allocationId>(eipalloc-[^<]+)<\/allocationId>/) || [])[1];
      const pubIp = (item.match(/<publicIp>([^<]+)<\/publicIp>/) || [])[1] || "unknown";
      const assocId = (item.match(/<associationId>([^<]+)<\/associationId>/) || [])[1];
      const instId = (item.match(/<instanceId>([^<]+)<\/instanceId>/) || [])[1];
      if (allocId && !assocId && !instId) {
        findings.push({
          resource_id: allocId,
          resource_type: "elastic_ip",
          region,
          reason: `Unassociated Elastic IP (${pubIp} not attached to any instance or ENI)`,
          estimated_monthly_cost: 3.65,
        });
      }
    }
  } catch (err) {
    warnings.push(`ec2:DescribeAddresses failed: ${err.message}`);
  }

  const totalCost = Math.round(findings.reduce((s, x) => s + x.estimated_monthly_cost, 0) * 100) / 100;
  return {
    region,
    mode: "live",
    waste_count: findings.length,
    total_estimated_monthly_cost: totalCost,
    resources: findings,
    warnings,
  };
}

async function liveWhoCreated(resourceId, region, creds) {
  if (creds.mode === "fixture" || !creds.accessKeyId || !creds.secretAccessKey) {
    const match = DEMO_DATA.waste.resources.find(r => r.resource_id === resourceId) || DEMO_DATA.waste.resources[0];
    return {
      resource_id: match.resource_id,
      found: true,
      event_name: match.creating_event,
      user: match.created_by,
      event_time: match.created_at,
      source_ip: match.source_ip,
    };
  }

  const qLower = resourceId.toLowerCase();
  const isOpenSearch = qLower.includes("opensearch");
  const lookupAttrs = isOpenSearch
    ? [
        { AttributeKey: "EventSource", AttributeValue: "aoss.amazonaws.com" },
        { AttributeKey: "EventSource", AttributeValue: "es.amazonaws.com" },
      ]
    : [{ AttributeKey: "ResourceName", AttributeValue: resourceId }];

  for (const attr of lookupAttrs) {
    try {
      const rawText = await awsSignedPost({
        service: "cloudtrail",
        region,
        host: `cloudtrail.${region}.amazonaws.com`,
        contentType: "application/x-amz-json-1.1",
        amzTarget: "com.amazonaws.cloudtrail.v20131101.CloudTrail_20131101.LookupEvents",
        body: JSON.stringify({ LookupAttributes: [attr], MaxResults: 25 }),
        creds,
      });
      const ctData = JSON.parse(rawText);
      const events = (ctData.Events || []).filter(
        e => !String(e.EventName || "").startsWith("Describe") && !String(e.EventName || "").startsWith("List") && !String(e.EventName || "").startsWith("Get")
      );
      const chosen = events[0] || (ctData.Events || [])[0];
      if (chosen) {
        let detail = {};
        try { detail = JSON.parse(chosen.CloudTrailEvent || "{}"); } catch (_) {}
        const user =
          chosen.Username ||
          detail?.userIdentity?.userName ||
          detail?.userIdentity?.principalId ||
          detail?.userIdentity?.arn ||
          "unknown";
        const resName = (chosen.Resources && chosen.Resources[0]?.ResourceName) || resourceId;
        const evTime = chosen.EventTime
          ? new Date(chosen.EventTime * 1000).toISOString()
          : detail.eventTime || "recent";
        return {
          resource_id: resName,
          region,
          found: true,
          event_name: chosen.EventName,
          user,
          event_time: evTime,
          source_ip: detail.sourceIPAddress || "N/A",
        };
      }
    } catch (_) {}
  }

  return {
    resource_id: resourceId,
    region,
    found: false,
    event_name: "NotFoundInCloudTrail",
    user: "Not found in CloudTrail (90d history)",
    event_time: null,
    message: `No CloudTrail creation event found for '${resourceId}' in ${region}.`,
  };
}

async function liveExplainChange(resourceId, region, creds) {
  const waste = await liveFindWaste(region, creds);
  const matchingWaste = (waste.resources || []).find(r => r.resource_id === resourceId);
  const who = await liveWhoCreated(resourceId, region, creds);

  if (matchingWaste) {
    const cost = matchingWaste.estimated_monthly_cost;
    return {
      resource_id: resourceId,
      region,
      is_waste: true,
      estimated_monthly_cost: cost,
      created_by: who.user,
      creating_event: who.event_name,
      created_at: who.event_time,
      attribution: who,
      explanation: `Resource ${resourceId} adds an estimated $${cost.toFixed(2)}/month to your AWS bill because it is flagged as waste: ${matchingWaste.reason}. According to CloudTrail, it was created by '${who.user}' via ${who.event_name} on ${who.event_time}.`,
    };
  }

  // Check if it matches a Cost Explorer service
  const ce = await liveCostSummary(30, "SERVICE", creds);
  const svcMatch = (ce.breakdown || []).find(
    s => s.group.toLowerCase().includes(resourceId.toLowerCase()) || resourceId.toLowerCase().includes(s.group.toLowerCase())
  );

  if (svcMatch) {
    const explanation = who.found
      ? `${svcMatch.group} generated $${svcMatch.cost_usd.toFixed(2)} in AWS spend over the last 30 days. CloudTrail shows resource '${who.resource_id}' in ${region} was created/modified by '${who.user}' via ${who.event_name} on ${who.event_time}.`
      : `${svcMatch.group} generated $${svcMatch.cost_usd.toFixed(2)} in AWS spend over the last 30 days in ${region}.`;
    return {
      resource_id: who.resource_id || resourceId,
      region,
      is_waste: true,
      estimated_monthly_cost: svcMatch.cost_usd,
      created_by: who.user,
      creating_event: who.event_name,
      created_at: who.event_time,
      attribution: who,
      explanation,
    };
  }

  return {
    resource_id: who.resource_id || resourceId,
    region,
    is_waste: false,
    estimated_monthly_cost: 0,
    created_by: who.user,
    creating_event: who.event_name,
    created_at: who.event_time,
    attribution: who,
    explanation: who.found
      ? `Resource ${who.resource_id} is not flagged as waste in ${region} ($0.00/mo waste). It was created by '${who.user}' via ${who.event_name} on ${who.event_time}.`
      : `Resource ${resourceId} was not found among wasteful resources in ${region} ($0.00/mo waste), and no matching creation event was found in CloudTrail.`,
  };
}

export default async (req) => {
  const url = new URL(req.url);
  const path = url.pathname;
  let bodyObj = {};
  if (req.method === "POST") {
    try { bodyObj = await req.json(); } catch (_) {}
  }

  const creds = getCredsFromReq(req, bodyObj);
  const jsonHeaders = { "Content-Type": "application/json" };

  if (path.endsWith("/aws_status") || path.endsWith("/aws_config")) {
    const status = await verifyStsIdentity(creds);
    return new Response(JSON.stringify(status), { status: 200, headers: jsonHeaders });
  }

  if (path.endsWith("/aws_disconnect") || path.endsWith("/llm_config")) {
    return new Response(JSON.stringify({ ok: true, mode: "fixture" }), { status: 200, headers: jsonHeaders });
  }

  if (path.endsWith("/cost_summary")) {
    const days = parseInt(url.searchParams.get("days") || bodyObj.days || "30", 10);
    const groupBy = url.searchParams.get("group_by") || bodyObj.group_by || "SERVICE";
    const res = await liveCostSummary(days, groupBy, creds);
    return new Response(JSON.stringify(res), { status: 200, headers: jsonHeaders });
  }

  if (path.endsWith("/find_waste")) {
    const region = url.searchParams.get("region") || bodyObj.region || creds.region;
    const res = await liveFindWaste(region, creds);
    return new Response(JSON.stringify(res), { status: 200, headers: jsonHeaders });
  }

  if (path.endsWith("/who_created")) {
    const rid = url.searchParams.get("resource_id") || bodyObj.resource_id || "";
    const region = url.searchParams.get("region") || bodyObj.region || creds.region;
    const res = await liveWhoCreated(rid, region, creds);
    return new Response(JSON.stringify(res), { status: 200, headers: jsonHeaders });
  }

  if (path.endsWith("/explain_change")) {
    const rid = url.searchParams.get("resource_id") || bodyObj.resource_id || "";
    const region = url.searchParams.get("region") || bodyObj.region || creds.region;
    const res = await liveExplainChange(rid, region, creds);
    return new Response(JSON.stringify(res), { status: 200, headers: jsonHeaders });
  }

  if (path.endsWith("/ask")) {
    const query = bodyObj.query || "Why did my AWS bill grow?";
    const region = bodyObj.region || creds.region;
    const [svcSum, regSum, usgSum, wasteRes] = await Promise.all([
      liveCostSummary(30, "SERVICE", creds),
      liveCostSummary(30, "REGION", creds),
      liveCostSummary(30, "USAGE_TYPE", creds),
      liveFindWaste(region, creds),
    ]);

    let topServiceAttribution = null;
    if (svcSum.breakdown && svcSum.breakdown.length > 0) {
      const topSvc = svcSum.breakdown[0].group;
      const topReg = (regSum.breakdown && regSum.breakdown[0]?.group && regSum.breakdown[0].group !== "NoRegion")
        ? regSum.breakdown[0].group
        : region;
      topServiceAttribution = await liveExplainChange(topSvc, topReg, creds);
    }

    const toolContext = {
      total_cost_usd: svcSum.total_cost_usd,
      credits_applied_usd: svcSum.credits_applied_usd,
      net_after_credits_usd: svcSum.net_after_credits_usd,
      cost_by_service: (svcSum.breakdown || []).slice(0, 6),
      cost_by_region: (regSum.breakdown || []).slice(0, 5),
      cost_by_usage_type: (usgSum.breakdown || []).slice(0, 6),
      waste_in_region: wasteRes,
      top_service_attribution: topServiceAttribution,
    };

    try {
      const llmResp = await fetch("https://text.pollinations.ai/openai", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "openai",
          messages: [
            {
              role: "system",
              content: "You are CloudBill Detective, a strictly read-only AWS FinOps AI assistant. Answer clearly using the exact dollar amounts, regions, resource IDs, and CloudTrail creators from the MCP tool results.",
            },
            {
              role: "user",
              content: `User Question: ${query}\n\nLive MCP Tool Output (JSON):\n${JSON.stringify(toolContext, null, 2)}`,
            },
          ],
        }),
      });
      const llmJson = await llmResp.json();
      const answer = (llmJson.choices?.[0]?.message?.content || "").replace(/<thinking>[\s\S]*?<\/thinking>\s*/gi, "").trim();
      return new Response(
        JSON.stringify({
          answer,
          report: {
            title: "Free Cloud LLM ($0 Cost • No AWS Bedrock)",
            tools_used: [
              "cost_summary(30, 'SERVICE')",
              "cost_summary(30, 'REGION')",
              "cost_summary(30, 'USAGE_TYPE')",
              `find_waste('${region}')`,
            ],
          },
        }),
        { status: 200, headers: jsonHeaders }
      );
    } catch (_) {
      return new Response(
        JSON.stringify({
          answer: topServiceAttribution?.explanation || `30-day AWS spend is $${svcSum.total_cost_usd.toFixed(2)}.`,
          report: {
            title: "CloudBill Detective",
            tools_used: ["cost_summary(30, 'SERVICE')", `find_waste('${region}')`],
          },
        }),
        { status: 200, headers: jsonHeaders }
      );
    }
  }

  return new Response(JSON.stringify({ status: "ok" }), { status: 200, headers: jsonHeaders });
};

export const config = {
  path: "/tools/*",
};

