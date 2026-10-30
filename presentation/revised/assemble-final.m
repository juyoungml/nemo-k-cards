// Assemble the nine-page revised presentation from temporary rendered images.
#import <AppKit/AppKit.h>
#import <ImageIO/ImageIO.h>
static CGImageRef load(NSString *path){CGImageSourceRef src=CGImageSourceCreateWithURL((__bridge CFURLRef)[NSURL fileURLWithPath:path],NULL);if(!src){fprintf(stderr,"Missing page: %s\n",path.UTF8String);exit(2);}CGImageRef image=CGImageSourceCreateImageAtIndex(src,0,NULL);CFRelease(src);return image;}
static CGImageRef compressed(CGImageRef image){NSBitmapImageRep*rep=[[NSBitmapImageRep alloc]initWithCGImage:image];NSData*bytes=[rep representationUsingType:NSBitmapImageFileTypeJPEG properties:@{NSImageCompressionFactor:@0.96}];CGDataProviderRef p=CGDataProviderCreateWithCFData((__bridge CFDataRef)bytes);CGImageRef result=CGImageCreateWithJPEGDataProvider(p,NULL,true,kCGRenderingIntentDefault);CGDataProviderRelease(p);return result;}
int main(int argc,const char**argv){@autoreleasepool{
 if(argc!=3)return 1;NSString*root=[NSString stringWithUTF8String:argv[1]],*dir=[root stringByAppendingPathComponent:@"presentation/revised"];
 NSString*build=[NSString stringWithUTF8String:argv[2]];
 int mapping[]={1,2,3,4,0,7,8,9,10};CGRect media=CGRectMake(0,0,1600,900);
 NSDictionary*meta=@{(__bridge NSString*)kCGPDFContextTitle:@"Nemo K Cards - Integrated presentation",(__bridge NSString*)kCGPDFContextSubject:@"Approved combined 04/05, nine pages including cover"};
 CGContextRef pdf=CGPDFContextCreateWithURL((__bridge CFURLRef)[NSURL fileURLWithPath:[dir stringByAppendingPathComponent:@"Nemo-K-Cards-Revised.pdf"]],&media,(__bridge CFDictionaryRef)meta);
 for(int page=0;page<9;page++){@autoreleasepool{
  NSString*path=mapping[page]?[build stringByAppendingPathComponent:[NSString stringWithFormat:@"slide-%02d.png",mapping[page]]]:[build stringByAppendingPathComponent:@"merged-slide.png"];
  CGImageRef source=load(path);CGColorSpaceRef cs=CGColorSpaceCreateDeviceRGB();CGContextRef canvas=CGBitmapContextCreate(NULL,2400,1350,8,0,cs,kCGImageAlphaPremultipliedLast);CGColorSpaceRelease(cs);
  CGContextDrawImage(canvas,CGRectMake(0,0,2400,1350),source);CGImageRelease(source);
  if(page>=4){
   CGContextSaveGState(canvas);CGContextScaleCTM(canvas,1.5,1.5);CGContextTranslateCTM(canvas,0,900);CGContextScaleCTM(canvas,1,-1);
   NSGraphicsContext.currentContext=[NSGraphicsContext graphicsContextWithCGContext:canvas flipped:YES];
   [[NSColor colorWithDeviceRed:244/255. green:243/255. blue:235/255. alpha:1]setFill];NSRectFill(NSMakeRect(50,24,650,52));
   NSString*label=[NSString stringWithFormat:@"WHAT’S ON KOREA / %02d",page];
   [label drawAtPoint:NSMakePoint(60,40) withAttributes:@{NSFontAttributeName:[NSFont fontWithName:@"AppleSDGothicNeo-Bold" size:18],NSForegroundColorAttributeName:[NSColor colorWithDeviceRed:80/255. green:120/255. blue:54/255. alpha:1]}];
   NSGraphicsContext.currentContext=nil;CGContextRestoreGState(canvas);
  }
  CGImageRef image=CGBitmapContextCreateImage(canvas);
  CGImageRef jpg=compressed(image);CGPDFContextBeginPage(pdf,NULL);CGContextDrawImage(pdf,media,jpg);CGPDFContextEndPage(pdf);
  CGImageRelease(jpg);CGImageRelease(image);CGContextRelease(canvas);
 }}
 CGPDFContextClose(pdf);CGContextRelease(pdf);puts("Integrated final deck: 9 pages (cover plus slides 01-08).");
}return 0;}
