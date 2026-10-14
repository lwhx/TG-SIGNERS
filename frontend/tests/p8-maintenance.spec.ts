import {expect,test} from "@playwright/test";

test("P8 protected settings backup creation, imported archive, staged restore and performance",async ({page})=>{
  let backups:any[]=[];
  let pending:any=null;
  let last:any=null;
  let measured=0;
  const policy={
    enabled:false,run_days:90,relay_sent_days:90,relay_failed_days:180,
    alert_enabled:true,recovery_enabled:false,alert_window_minutes:5,
    alert_cooldown_minutes:30,
  };
  const sample={
    measured_at:"2026-10-15T01:00:00Z",architecture:"aarch64",
    dashboard:{present:true,bytes:2048,wal_bytes:0,p50_ms:0.01,p95_ms:0.02},
    relay:{present:true,bytes:4096,wal_bytes:0,p50_ms:0.02,p95_ms:0.03},
    disk_free_mb:1024,disk_total_mb:2048,process_rss_peak_mb:128,
    process_cpu_seconds:2,probe_ms:0.7,
  };
  await page.route("**/api/**",async route=>{
    const path=new URL(route.request().url()).pathname;
    const method=route.request().method();
    const json=(data:any,status=200)=>route.fulfill({status,json:data});
    if(path==="/api/panel/me")return json({});
    if(path==="/api/accounts")return json([]);
    if(path==="/api/tasks")return json([]);
    if(path==="/api/overview")return json({trend:[],recent:[]});
    if(path==="/api/settings")return json({
      global_proxy:"",proxy_display:"",timezone:"Asia/Shanghai",
      ai_key:"",ai_model:"",ai_base_url:"",notifications:[],
      tg_signer_version:"3.0",
    });
    if(path==="/api/maintenance")return json({
      policy,batch_size:500,last_cleanup:null,
      alerts:{last_status:"idle"},recovery_pending:false,
    });
    if(path==="/api/relay/retry-policy")return json({
      policy:{enabled:false,max_attempts:3,base_delay:60,max_delay:600,horizon_hours:24},
    });
    if(path==="/api/maintenance/backups"&&method==="GET")return json({items:backups});
    if(path==="/api/maintenance/backups/restore-status")return json({pending,last});
    if(path==="/api/maintenance/backups"&&method==="POST"){
      const id="1".repeat(32);
      backups=[{id,created_at:"2026-10-15T00:00:00Z",size:2048}];
      return json({id,created_at:"2026-10-15T00:00:00Z",files:5,size:2048},201);
    }
    if(path==="/api/maintenance/backups/"+("1".repeat(32))+"/verify"){
      return json({valid:true,files:5,created_at:"2026-10-15T00:00:00Z",total_size:4096});
    }
    if(path==="/api/maintenance/backups/"+("1".repeat(32))+"/restore"){
      const payload=route.request().postDataJSON();
      expect(payload.confirmation).toBe("RESTORE");
      pending={id:"1".repeat(32),created_at:"2026-10-15T02:00:00Z"};
      return json({pending:true,id:pending.id,files:5});
    }
    if(path==="/api/maintenance/performance")return json({
      current:sample,history:measured?[sample]:[],
    });
    if(path==="/api/maintenance/performance/schema")return json({
      current:3,supported:3,migrations:[
        {version:1,description:"P6",applied_at:"2026-10-15T00:00:00Z"},
        {version:2,description:"P7",applied_at:"2026-10-15T00:00:00Z"},
        {version:3,description:"P8",applied_at:"2026-10-15T00:00:00Z"},
      ],
    });
    if(path==="/api/maintenance/performance/measure"){
      measured++;return json(sample);
    }
    return json({});
  });
  await page.goto("/");
  await page.locator("nav").getByRole("button",{name:"系统设置"}).click();
  await expect(page.getByText("加密备份与恢复")).toBeVisible();
  await expect(page.getByText("运行性能基线")).toBeVisible();
  await expect(page.getByText("当前版本 v3",{exact:false})).toBeVisible();
  await page.getByLabel("备份加密密码").fill("passphrase-for-P8-tests");
  await page.getByRole("button",{name:"创建加密备份"}).click();
  await expect(page.getByText("1".repeat(12),{exact:false})).toBeVisible();
  await page.getByLabel("备份加密密码").fill("passphrase-for-P8-tests");
  await page.getByRole("button",{name:"校验",exact:true}).click();
  await page.getByLabel("备份加密密码").fill("passphrase-for-P8-tests");
  page.once("dialog",dialog=>dialog.accept());
  await page.getByRole("button",{name:"暂存恢复"}).click();
  await expect(page.getByText("待冷启动恢复：",{exact:false})).toBeVisible();
  await page.getByRole("button",{name:"记录一次性能采样"}).click();
  await expect.poll(()=>measured).toBe(1);
});
