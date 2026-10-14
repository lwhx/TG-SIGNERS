import {expect, test} from "@playwright/test";

test("P7 Bot results, album statistics, backfill preflight and explicit start (mock API)", async ({page}) => {
  const account = {
    id: 44, name:"Mock account", phone:"+10000000000",
    username:"demo",user_id:"44", avatar:"",status:"online", proxy:"",last_checked:null,
  };
  const task = {
    id: 55, account_id:44, name:"115 存储", kind:"automation", enabled:true,
    group_name:"115", folder:"", config:{rules:[{
      id:"p7", enabled:true,
      triggers:[{type:"message",params:{chat_ids:["@source"]}}],
      handlers:[{handler:"relay_message",params:{targets:[{
        chat_id:"@115bot",mode:"extract",pattern:"(https?://[^\\s]+)",
        dedup:"message",track_bot_result:true,
      }]}}],
    }]},
  };
  let previews=0;
  let starts=0;
  const jobs: any[]=[];
  await page.route("**/api/**",async route=>{
    const url=new URL(route.request().url());
    const path=url.pathname;
    if(path==="/api/panel/me") return route.fulfill({json:{}});
    if(path==="/api/accounts") return route.fulfill({json:[account]});
    if(path==="/api/tasks") return route.fulfill({json:[task]});
    if(path==="/api/overview") return route.fulfill({json:{trend:[],recent:[]}});
    if(path==="/api/relay/bot-results") return route.fulfill({json:{
      page:1,page_size:20,total:1,items:[{
        destination:"@115bot",fingerprint:"a".repeat(64),
        account_id:44,task_id:55,bot_chat:777,sent_message:12,
        reply_message:33,deadline:20,status:"succeeded",created_at:10,updated_at:15,
      }],
    }});
    if(path==="/api/relay/media-groups/stats")return route.fulfill({json:{
      sent:3,failed:0,sending:0,uncertain:1,
    }});
    if(path==="/api/relay/backfill/jobs")return route.fulfill({json:{items:jobs}});
    if(path==="/api/relay/backfill/preview"){
      previews++;
      const body=route.request().postDataJSON();
      return route.fulfill({json:{
        ...body, account_id:44,rules:["p7"],target_count:1,dry_run:true,
        warning:"仅检查配置，不读取或发送消息。",
      }});
    }
    if(path==="/api/relay/backfill/start"){
      starts++;
      const body=route.request().postDataJSON();
      expect(body.confirmation).toBe("BACKFILL");
      jobs.unshift({
        id:1,task_id:55,account_id:44,source_chat:"@source",
        before_id:null,next_before_id:null,batch_size:body.limit,
        status:"queued",scanned:0,matched:0,last_error:"",
        created_at:"2026-10-15T00:00:00+00:00",updated_at:"2026-10-15T00:00:00+00:00",
      });
      return route.fulfill({status:202,json:{id:1,status:"queued"}});
    }
    throw new Error("Unexpected request: "+path);
  });
  await page.goto("/");
  await page.locator("nav").getByRole("button",{name:"转发增强"}).click();
  await expect(page.getByRole("cell",{name:"Bot 确认成功"})).toBeVisible();
  await expect(page.getByText("整组已发送")).toBeVisible();
  await expect(page.getByText("3",{exact:true}).first()).toBeVisible();
  await page.getByLabel("增强功能任务").selectOption("55");
  await page.getByLabel("补抓消息来源").selectOption("@source");
  await page.getByRole("button",{name:"检查补抓范围"}).click();
  await expect.poll(()=>previews).toBe(1);
  await expect(page.getByText("预检通过：",{exact:false})).toBeVisible();
  page.once("dialog", dialog=>dialog.accept());
  await page.getByRole("button",{name:"确认启动该批次"}).click();
  await expect.poll(()=>starts).toBe(1);
  await expect(page.getByText("排队中",{exact:false})).toBeVisible();
});
