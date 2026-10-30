// Integrated research, card creation and approval slide.
// Arguments: repository root, temporary build directory.
#define main render_full_deck_unused
#include "render.m"
#undef main
static void captureRegion(NSString *name,CGFloat x,CGFloat y,CGFloat width,CGFloat sx,CGFloat sy,CGFloat sw,CGFloat sh){
 NSString *path=[root stringByAppendingPathComponent:[@"presentation/revised/assets/" stringByAppendingString:name]];
 NSImage *im=[[NSImage alloc]initWithContentsOfFile:path];NSBitmapImageRep *rep=[NSBitmapImageRep imageRepWithData:[NSData dataWithContentsOfFile:path]];
 if(!im||!rep){fprintf(stderr,"Missing supplied capture\n");exit(2);}
 NSSize size=im.size;CGFloat fx=size.width/rep.pixelsWide,fy=size.height/rep.pixelsHigh;
 NSRect crop=NSMakeRect(sx*fx,(rep.pixelsHigh-sy-sh)*fy,sw*fx,sh*fy);
 [im drawInRect:NSMakeRect(x,y,width,width*sh/sw) fromRect:crop operation:NSCompositingOperationSourceOver fraction:1 respectFlipped:YES hints:@{NSImageHintInterpolation:@(NSImageInterpolationHigh)}];
}
static void merged(void){
 box(0,0,W,H,paper);
 text(@"WHAT’S ON KOREA / 04",60,40,1480,18,YES,green);
 text(@"한국어 정보를 찾아, 영어 카드뉴스로 전합니다",60,98,1480,44,YES,ink);
 text(@"요청 예시  “인천공항에서 기후동행카드를 어디서 사는지 영어 카드로 알려줘.”",60,177,1480,25,NO,muted);
 text(@"01  공식 자료를 찾아 교차 확인",60,270,510,22,YES,green);
 text(@"02  영어 카드 제작",705,270,430,22,YES,green);
 text(@"03  검수 및 승인",1220,270,320,22,YES,green);
 // Original screenshot pixels are preserved; only the presentation viewport is cropped.
 text(@"서울시 단기권 안내",60,318,470,17,YES,muted);
 box(59,339,472,267,rgb(207,214,199));
 captureRegion(@"official-climate-ko-user.png",60,340,470,55,560,1850,1040);
 box(132,494,448,214,rgb(218,223,212));
 box(124,486,447,214,rgb(207,214,199));
 box(125,487,445,212,white);
 text(@"공식 구매 안내(영문)",137,497,420,17,YES,muted);
 captureRegion(@"official-climate-purchase-en-user.png",125,526,445,30,1010,1780,690);
 line(60,723,48,green);
 text(@"공항 이동은 별도 요금, 카드는 서울역에서 구매",60,748,510,22,YES,green);
 arrow(600,486,green);
 screenshot(@"climate-card-guide.png",705,305,400,1402);
 arrow(1140,486,green);
 text(@"출처·문구 확인\n운영자가 최종 승인",1220,390,320,28,YES,ink);
 text(@"승인 후 Instagram 게시",1220,520,320,21,NO,muted);
 text(@"왼쪽은 사용자 제공 공식 자료 캡처, 가운데는 직접 제작한 카드 시안입니다. 자동 생성·게시 실행 결과는 아닙니다. · 2026.10.30 확인",60,850,1480,15,NO,muted);
}

int main(int argc,const char**argv){@autoreleasepool{
 if(argc!=3)return 1;root=[NSString stringWithUTF8String:argv[1]];
 paper=rgb(244,243,235);ink=rgb(25,44,31);green=rgb(80,120,54);muted=rgb(105,115,100);pale=rgb(229,236,215);white=NSColor.whiteColor;red=rgb(154,76,56);pink=rgb(244,231,223);
 NSString*out=[NSString stringWithUTF8String:argv[2]];
 CGColorSpaceRef cs=CGColorSpaceCreateDeviceRGB();CGContextRef canvas=CGBitmapContextCreate(NULL,2400,1350,8,0,cs,kCGImageAlphaPremultipliedLast);CGColorSpaceRelease(cs);CGContextScaleCTM(canvas,1.5,1.5);CGContextTranslateCTM(canvas,0,H);CGContextScaleCTM(canvas,1,-1);NSGraphicsContext.currentContext=[NSGraphicsContext graphicsContextWithCGContext:canvas flipped:YES];merged();NSGraphicsContext.currentContext=nil;
 CGImageRef im=CGBitmapContextCreateImage(canvas);NSBitmapImageRep*r=[[NSBitmapImageRep alloc]initWithCGImage:im];[[r representationUsingType:NSBitmapImageFileTypePNG properties:@{}]writeToFile:[out stringByAppendingPathComponent:@"merged-slide.png"] atomically:YES];
 CGImageRelease(im);CGContextRelease(canvas);
 puts("Rendered integrated slide into the temporary build directory.");
}return 0;}
