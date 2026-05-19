#import <UIKit/UIKit.h>

@interface MoneyOverlayManager : NSObject

@property (class, readonly) MoneyOverlayManager *sharedManager;

- (void)setupOverlay;

@end
