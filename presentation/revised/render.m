// Editable base-page revisions. Build with ./rerender.sh.
// Arguments: repository root, temporary build directory.
// Original PDF pages are reused without changing the source file; additions are native PDF text and shapes.
#import <AppKit/AppKit.h>
#import <PDFKit/PDFKit.h>
static NSColor *paper,*ink,*green,*muted,*pale,*white,*red,*pink;
static CGContextRef ctx;
static NSString *root;
static CGFloat H=900,W=1600;
static NSColor *rgb(int r,int g,int b){return [NSColor colorWithDeviceRed:r/255. green:g/255. blue:b/255. alpha:1];}
static void box(CGFloat x,CGFloat y,CGFloat w,CGFloat h,NSColor *c){[c setFill];NSRectFill(NSMakeRect(x,y,w,h));}
static void text(NSString*s,CGFloat x,CGFloat y,CGFloat w,CGFloat size,BOOL bold,NSColor*c){
 NSMutableParagraphStyle *p=[NSMutableParagraphStyle new];p.lineSpacing=5;p.lineBreakMode=NSLineBreakByWordWrapping;
 NSFont *font=[NSFont fontWithName:bold?@"AppleSDGothicNeo-Bold":@"AppleSDGothicNeo-Regular" size:size];
 NSDictionary *a=@{NSFontAttributeName:font?:[NSFont systemFontOfSize:size],NSForegroundColorAttributeName:c,NSParagraphStyleAttributeName:p};
 NSRect b=[s boundingRectWithSize:NSMakeSize(w,2000) options:NSStringDrawingUsesLineFragmentOrigin attributes:a];
 if(y+b.size.height>H-12)fprintf(stderr,"Text exceeds slide: %s\n",s.UTF8String);
 [s drawInRect:NSMakeRect(x,y,w,ceil(b.size.height)+5) withAttributes:a];
}
static void line(CGFloat x,CGFloat y,CGFloat w,NSColor*c){box(x,y,w,1,c);}
static void header(int n,NSString*title,NSString*sub){text([NSString stringWithFormat:@"WHAT'S ON KOREA / %02d",n],60,40,1300,18,YES,green);text(title,60,98,1480,44,YES,ink);text(sub,60,169,1480,25,NO,muted);}
static void foot(NSString*s){text(s,60,831,1480,17,NO,muted);}
static void arrow(CGFloat x,CGFloat y,NSColor*c){text(@"→",x,y,60,44,YES,c);}
static void tag(NSString*s,CGFloat x,CGFloat y,CGFloat w,NSColor *bg,NSColor*fg){box(x,y,w,36,bg);text(s,x+12,y+6,w-24,18,YES,fg);}
static void node(CGFloat x,CGFloat y,CGFloat w,CGFloat h,NSString*label,NSString*title,NSString*body,NSColor*bg){box(x,y,w,h,bg);text(label,x+22,y+18,w-44,17,YES,green);text(title,x+22,y+53,w-44,29,YES,ink);text(body,x+22,y+101,w-44,22,NO,ink);}
static void screenshot(NSString *name,CGFloat x,CGFloat y,CGFloat width,CGFloat topPixels){
 NSString *path=[root stringByAppendingPathComponent:[@"presentation/revised/assets/" stringByAppendingString:name]];
 NSImage *im=[[NSImage alloc]initWithContentsOfFile:path];
 NSBitmapImageRep *rep=[NSBitmapImageRep imageRepWithData:[NSData dataWithContentsOfFile:path]];
 if(!im||!rep){fprintf(stderr,"Missing screenshot: %s\n",path.UTF8String);exit(2);}
 CGFloat fraction=MIN(1,topPixels/rep.pixelsHigh),height=width*rep.pixelsHigh*fraction/rep.pixelsWide;
 NSSize sz=im.size;NSRect crop=NSMakeRect(0,sz.height*(1-fraction),sz.width,sz.height*fraction);
 [im drawInRect:NSMakeRect(x,y,width,height) fromRect:crop operation:NSCompositingOperationSourceOver fraction:1 respectFlipped:YES hints:@{NSImageHintInterpolation:@(NSImageInterpolationHigh)}];
}
static void revise(int page){switch(page){
 case 0:{
  // Rebuild the cover completely to avoid fragments from the original PDF's text.
  box(0,0,W,H,paper);
  text(@"FASTCAMPUS × NVIDIA / AGENTIC AI HACKATHON",70,57,1450,20,YES,green);
  text(@"Nemo K Cards",70,218,1000,104,YES,ink);
  text(@"Instagram @whatsonkorea",70,361,1000,56,YES,green);
  text(@"한국 생활·여행 정보를 영어 카드뉴스로 전하는",70,505,990,32,NO,ink);
  text(@"콘텐츠 운영 에이전트",70,556,970,32,YES,ink);
  text(@"조사와 제작은 에이전트에게, 최종 게시 승인은 운영자에게.",70,736,1030,24,NO,ink);
  NSImage *card=[[NSImage alloc]initWithContentsOfFile:[root stringByAppendingPathComponent:@"docs/assets/cards/seoul-forest.jpg"]];
  if(!card){fprintf(stderr,"Missing cover card\n");exit(2);}
  [card drawInRect:NSMakeRect(1130,240,400,500) fromRect:NSZeroRect operation:NSCompositingOperationSourceOver fraction:1 respectFlipped:YES hints:@{NSImageHintInterpolation:@(NSImageInterpolationHigh)}];
  text(@"서울숲 카드뉴스",1130,759,400,18,NO,muted);
  text(@"2026.10.30  ·  기능 업데이트 반영본",70,847,1200,18,NO,muted);
  break;
 }
 case 1:{
  box(55,85,1490,160,paper);
  text(@"자료 기반 생성부터 검수·게시까지 연결합니다",60,99,1480,44,YES,ink);
  text(@"한국 생활·여행 정보를 영어로 전하는 Instagram 계정과 콘텐츠 운영 에이전트입니다.",60,171,1480,25,NO,muted);
  box(55,265,580,470,paper);
  box(60,285,560,440,pale);
  text(@"콘텐츠를 만드는 쪽",88,309,510,18,YES,green);text(@"카드뉴스 에이전트",88,355,510,35,YES,ink);
  // Features as of #19–#27 and #23 on upstream main: five lines instead of three.
  box(60,440,560,280,pale);
  text(@"링크·파일·메모로 카드 생성",88,446,510,24,YES,ink);
  text(@"행사 카드 · 공식 출처 가이드 카드",88,500,510,24,YES,ink);
  text(@"근거 확인·편집·사진 교체·재작성",88,554,510,24,YES,ink);
  text(@"안전검사를 통과한 버전만 승인",88,608,510,24,YES,ink);
  text(@"즉시 또는 예약 게시",88,662,510,24,YES,ink);
  box(855,418,650,42,white);
  text(@"한국 생활·여행 정보를 읽고 저장하는 계정",865,425,640,23,NO,muted);
  box(60,765,1480,110,paper);
  text(@"Admin에서 내용을 검수·수정하고, NVIDIA OpenShell로 에이전트의 실행 권한을 제한합니다.",60,777,1480,24,NO,ink);
  foot(@"코드 반영 기준의 기능 설명입니다. 오른쪽 이미지는 기존 카드 시안이며, 최신 기능의 실배포 검증 결과는 아닙니다.");break;
 }
 case 2:{
  box(0,0,W,H,paper);
  header(2,@"한국을 즐기는 데 필요한 정보, 어디서 확인하나요?",@"행사 탐색부터 교통·결제 이용 안내까지, 실제 Reddit에 올라온 질문입니다.");
  text(@"관광·행사",60,251,430,25,YES,green);
  text(@"r/koreatravel · 2026.06.16",60,295,435,20,YES,muted);
  text(@"“방문 기간에 열리는 팝업과 행사는\n어디서 확인하나요?”",60,348,470,30,YES,ink);
  screenshot(@"reddit-popups-user.png",540,240,1000,280);
  line(60,461,1480,rgb(207,214,199));
  text(@"교통·결제 안내",60,520,440,25,YES,green);
  text(@"r/seoul · 2026.08.23",60,564,440,20,YES,muted);
  text(@"“기후동행카드는 어디서 사나요?”",60,619,435,30,YES,ink);
  screenshot(@"reddit-climate-purchase-user.png",540,485,1000,490);
  text(@"사용자 제공 Reddit 원문 캡처 · 왼쪽은 질문의 한국어 요약입니다. 개별 사례이며 전체 이용자를 대표하지 않습니다.",60,865,1480,15,NO,muted);
  break;
 }
 case 3:{
  // Use equal chart styling without a bold tourism label or highlighted bar.
  box(55,85,1490,140,paper);
  text(@"한국 생활·여행 정보를 찾고 이해하는 부담을 줄이려 합니다",60,99,1480,42,YES,ink);
  text(@"정보를 찾는 어려움과 낯선 이용 조건을, 출처가 있는 안내로 풀어내려 합니다.",60,171,1480,25,NO,muted);
  box(55,410,275,55,paper);
  text(@"관광 정보·안내",60,420,270,28,NO,ink);
  box(340,420,660*16.4/30,37,rgb(166,182,154));
  NSString *values[] = {@"27.8%", @"16.4%", @"13.1%", @"12.0%"};
  for(int row=0;row<4;row++){
   box(1028,310+95*row,125,60,paper);
   text(values[row],1035,325+95*row,125,33,NO,ink);
  }
  // Repaint both original lines as one text group so no old glyph fragments remain.
  box(1190,350,365,110,paper);
  text(@"한국어 정보 탐색",1200,357,350,30,YES,ink);
  text(@"생활·여행 정보 이해",1200,399,350,30,YES,ink);
  break;
 }
 case 6:{
  // Slide 05: the Safeguard. Redrawn completely (the original page's screenshot and text are covered).
  // Admin capture: current UI on demo data (the "Tank Day" case); see README "Slide 05 assets".
  box(0,0,W,H,paper);
  header(5,@"해로운 표현은 승인 단계까지 가지 못합니다",@"출처·링크·품질 검사는 운영자에게 알리고, 안전검사(Safeguard)의 차단은 누구도 덮어쓸 수 없습니다.");
  text(@"실제 Admin 화면 · 차단된 덱",60,236,800,18,YES,green);
  screenshot(@"safeguard-blocked-admin.png",60,268,800,100000);
  screenshot(@"safeguard-approve-locked.png",60,686,300,100000);
  text(@"← 승인 버튼도 잠깁니다.\nOverride 사유를 적어도 승인할 수 없습니다.",378,688,520,19,NO,ink);
  text(@"01  두 겹으로 검사",940,236,600,24,YES,green);
  text(@"금지 표현·기념일 규칙은 백엔드가 직접 찾고(모델이 뒤집지 못함),\nSafeguard 에이전트가 문구·캡션·해시태그와\n렌더링된 카드 이미지까지 읽고 판단합니다.",940,276,600,20,NO,ink);
  text(@"02  차단은 누구도 승인할 수 없음",940,388,600,24,YES,green);
  text(@"관리자도, Override도 예외가 아닙니다.\n검사가 실패하거나 응답이 없으면 차단합니다(fail-closed).",940,428,600,20,NO,ink);
  text(@"03  고친 버전은 다시 검사",940,512,600,24,YES,green);
  text(@"판정은 검사한 버전과 내용 해시에 묶입니다.\n문구·사진을 바꾸면 새 버전이 다시 통과해야 승인할 수 있습니다.",940,552,600,20,NO,ink);
  text(@"04  이유와 수정안까지",940,636,600,24,YES,green);
  text(@"문제 문구, 위반 규칙, 왜 이 독자에게 해로운지,\n어떻게 고칠지를 함께 보여줍니다.",940,676,600,20,NO,ink);
  line(60,768,1480,rgb(207,214,199));
  text(@"차단 범주",60,784,120,19,YES,green);
  text(@"혐오·괴롭힘 · 성적 콘텐츠 · 폭력·자해 · 불법·위험 행위 · 비극의 희화화 · 정치적 편들기 · 영토·문화 왜곡 · 사기",160,784,1380,19,YES,ink);
  foot(@"화면 속 덱은 시연용 데이터(‘탱크데이’ 사례)이며 판정 문구는 프로젝트의 고정 테스트 사례입니다. 그 밖의 검사는 경고만 하고 운영자가 판단합니다.");
  break;
 }
 case 9:{
  box(0,0,W,H,paper);
  header(9,@"제작부터 승인한 버전의 게시까지 연결합니다",@"에이전트의 실행 권한과 공개 계정의 게시 권한을 분리하고, 수정한 콘텐츠를 다시 검사합니다.");
  // Runtime row: NVIDIA environment and host separation, without implying a new deployment measurement.
  text(@"NVIDIA BREV · 팀 실행 환경의 배치 예시",60,247,1100,18,YES,green);
  node(60,289,370,232,@"OPERATOR",@"Admin · Next.js",@"자료 입력과 카드 검수\n문구·순서·캡션 편집\n통과한 버전 승인",white);
  node(493,289,490,232,@"TRUSTED HOST",@"FastAPI Backend",@"작업 제어·출처·렌더링\n콘텐츠 안전검사와 버전 관리\n승인한 버전만 게시 요청",white);
  node(1046,289,494,232,@"NVIDIA OPENSHELL",@"Claude Code 실행",@"조사·작성·검수\n파일·네트워크 접근 제한\nJSON 결과와 정책 로그 반환",pale);
  arrow(439,373,green);arrow(992,373,green);
  node(60,606,370,164,@"DATA / CREDENTIALS",@"Supabase",@"Postgres · Vault · Storage",white);
  node(493,606,490,164,@"PUBLISH GATE",@"안전검사 통과 + 사람 승인",@"확정한 카드·캡션 버전에 연결",pale);
  node(1046,606,494,164,@"INSTAGRAM GRAPH API",@"백엔드가 게시",@"토큰은 Vault, 사용은 백엔드만",white);
  arrow(992,661,green);
  text(@"↓",719,537,70,42,YES,green);
  text(@"저장·토큰·이미지 관리",60,558,370,21,NO,muted);
  text(@"에이전트에 제작을 맡기고, 근거 확인과 계정 통제를 운영자에게 남깁니다.",60,785,1480,27,YES,green);
  foot(@"현재 코드 기준 구성 요약입니다. Brev·OpenShell의 실행 구성과 실배포 연결 상태는 별도로 확인합니다.");break;
 }
}}
static void newpage(CGContextRef c,CGFloat width,CGFloat height){CGRect r=CGRectMake(0,0,width,height);CFDataRef d=CFDataCreate(NULL,(UInt8*)&r,sizeof(r));CFDictionaryRef opts=CFDictionaryCreate(NULL,(const void*[]){kCGPDFContextMediaBox},(const void*[]){d},1,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);CGPDFContextBeginPage(c,opts);CFRelease(opts);CFRelease(d);}
int main(int argc,const char**argv){@autoreleasepool{
 if(argc!=3)return 1;root=[NSString stringWithUTF8String:argv[1]];
 paper=rgb(244,243,235);ink=rgb(25,44,31);green=rgb(80,120,54);muted=rgb(105,115,100);pale=rgb(229,236,215);white=NSColor.whiteColor;red=rgb(154,76,56);pink=rgb(244,231,223);
 NSString *original=[root stringByAppendingPathComponent:@"presentation/Nemo-K-Cards-Presentation.pdf"];
 NSString *output=[[NSString stringWithUTF8String:argv[2]] stringByAppendingPathComponent:@"base.pdf"];
 CGPDFDocumentRef src=CGPDFDocumentCreateWithURL((__bridge CFURLRef)[NSURL fileURLWithPath:original]);if(!src){fprintf(stderr,"Source missing\n");return 2;}
 CGRect rect=CGRectMake(0,0,W,H);ctx=CGPDFContextCreateWithURL((__bridge CFURLRef)[NSURL fileURLWithPath:output],&rect,NULL);
 for(int i=0;i<10;i++){newpage(ctx,W,H);CGContextDrawPDFPage(ctx,CGPDFDocumentGetPage(src,i+1));CGContextSaveGState(ctx);CGContextTranslateCTM(ctx,0,H);CGContextScaleCTM(ctx,1,-1);NSGraphicsContext *old=NSGraphicsContext.currentContext;NSGraphicsContext.currentContext=[NSGraphicsContext graphicsContextWithCGContext:ctx flipped:YES];revise(i);NSGraphicsContext.currentContext=old;CGContextRestoreGState(ctx);CGPDFContextEndPage(ctx);}
 CGPDFContextClose(ctx);CGContextRelease(ctx);CGPDFDocumentRelease(src);
 puts("Rendered base pages into the temporary build directory.");
}return 0;}
